"""Upgrade command for paude sessions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.cli.app import BackendType, app
from paude.cli.helpers import find_session_backend

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.backends.base import Session
    from paude.backends.podman.backend import PodmanBackend
    from paude.container.runner import ContainerRunner
    from paude.upgrade_state import UpgradeManifest


@dataclass
class UpgradeOverrides:
    """CLI overrides for session configuration during upgrade."""

    otel_endpoint: str | None = None
    allowed_domains: list[str] | None = None
    gpu: str | None = None  # "" means explicitly disabled
    yolo: bool | None = None
    provider: str | None = None
    providers: list[str] | None = None
    agent_providers: dict[str, str] | None = None

    def has_changes(self) -> bool:
        """Return True if any override was specified."""
        return (
            self.otel_endpoint is not None
            or self.allowed_domains is not None
            or self.gpu is not None
            or self.yolo is not None
            or self.provider is not None
            or self.providers is not None
            or self.agent_providers is not None
        )


def _finalize_upgrade(name: str, session: Session | None) -> None:
    """Record a finished upgrade: refresh the registry entry, clear the marker.

    Shared by the success path and the stale-marker fast-path so both stay in
    sync. ``session`` is the live session used to refresh agent metadata (or
    ``None`` when the container is already gone).
    """
    from paude import __version__, upgrade_state
    from paude.registry import SessionRegistry

    SessionRegistry().refresh_from_session(name, session, __version__)
    upgrade_state.delete(name)


@app.command("upgrade")
def session_upgrade(
    name: Annotated[str, typer.Argument(help="Session name to upgrade")],
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Deprecated compatibility flag; upgrades always rebuild.",
        ),
    ] = False,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    otel_endpoint: Annotated[
        str | None,
        typer.Option(
            "--otel-endpoint",
            help=(
                "Set or change the OTLP collector endpoint "
                "(e.g., http://collector:4318). "
                'Use --otel-endpoint "" to remove.'
            ),
        ),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domains",
            help="Override allowed domains for network filtering.",
        ),
    ] = None,
    gpu: Annotated[
        str | None,
        typer.Option(
            "--gpu",
            help="Set or change GPU passthrough (e.g., all, device=0,1).",
        ),
    ] = None,
    no_gpu: Annotated[
        bool,
        typer.Option(
            "--no-gpu",
            help="Disable GPU passthrough.",
        ),
    ] = False,
    yolo: Annotated[
        bool,
        typer.Option("--yolo", help="Enable YOLO mode."),
    ] = False,
    no_yolo: Annotated[
        bool,
        typer.Option("--no-yolo", help="Disable YOLO mode."),
    ] = False,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Change inference provider (e.g., vertex, openai).",
        ),
    ] = None,
    providers: Annotated[
        list[str] | None,
        typer.Option(
            "--providers",
            help="Replace credential providers (comma-separated/repeatable).",
        ),
    ] = None,
    agent_provider: Annotated[
        list[str] | None,
        typer.Option(
            "--agent-provider",
            help="Replace mappings using AGENT=PROVIDER entries.",
        ),
    ] = None,
) -> None:
    """Upgrade a session to the current paude version.

    Can also reconfigure session options (e.g., --otel-endpoint, --gpu)
    without losing workspace or agent data. Upgrades always pull and rebuild
    agent tooling, including when the Paude version is unchanged.
    """
    from paude import __version__
    from paude.backends.podman.backend import PodmanBackend
    from paude.cli.helpers import _get_backend_instance

    # Resolve --gpu / --no-gpu
    cli_gpu: str | None = gpu
    if no_gpu:
        cli_gpu = ""  # empty string sentinel = explicitly disabled

    # Resolve --yolo / --no-yolo
    cli_yolo: bool | None = None
    if yolo:
        cli_yolo = True
    elif no_yolo:
        cli_yolo = False

    from paude.cli.helpers import (
        _parse_agent_provider_options,
        _split_list_option,
    )

    try:
        cli_agent_providers = _parse_agent_provider_options(agent_provider)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    if provider is not None and cli_agent_providers is not None:
        typer.echo("Error: Specify --provider or --agent-provider, not both.", err=True)
        raise typer.Exit(1)

    overrides = UpgradeOverrides(
        otel_endpoint=otel_endpoint,
        allowed_domains=allowed_domains,
        gpu=cli_gpu,
        yolo=cli_yolo,
        provider=provider,
        providers=_split_list_option(providers),
        agent_providers=cli_agent_providers,
    )

    from paude import upgrade_state

    # A leftover manifest means a previous upgrade was interrupted. Resume it,
    # even if the container it was replacing is already gone.
    manifest = upgrade_state.load(name)
    resuming = manifest is not None

    # Find session backend. When resuming, allow reconstruction from the
    # registry entry even if the container was already removed by the
    # interrupted run (so it can't be discovered live).
    if backend is not None:
        backend_obj = _get_backend_instance(backend)
    else:
        result = find_session_backend(name, allow_offline=resuming)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        backend, backend_obj = result

    # Get session — may be absent when resuming after the container was removed.
    session = backend_obj.get_session(name)
    if session is None and not resuming:
        typer.echo(f"Session '{name}' not found.", err=True)
        raise typer.Exit(1)

    session_version = session.version if session is not None else None

    has_overrides = overrides.has_changes()

    # Stale marker: a prior upgrade finished (container running at the target
    # version) but crashed before the manifest was cleared. Refresh the registry
    # and drop the marker without a needless rebuild. Only `running` qualifies:
    # any other status (e.g. a container created but never started, which podman
    # reports as `stopped`) means the upgrade did not finish, so fall through and
    # resume.
    if (
        resuming
        and not has_overrides
        and session is not None
        and session.version == __version__
        and session.status == "running"
    ):
        _finalize_upgrade(name, session)
        typer.echo(
            f"Session '{name}' is already at version {__version__}; "
            "cleared stale upgrade marker.",
        )
        return

    if resuming:
        typer.echo(f"Resuming interrupted upgrade of '{name}'...", err=True)
    elif has_overrides and session_version == __version__:
        typer.echo(
            f"Reconfiguring session '{name}' (version {__version__})...",
            err=True,
        )
    else:
        old_version = session_version or "unknown"
        typer.echo(
            f"Upgrading session '{name}' from {old_version} to {__version__}...",
            err=True,
        )

    # Auto-stop if running
    if session is not None and session.status == "running":
        typer.echo(f"Stopping session '{name}'...", err=True)
        backend_obj.stop_session(name)

    try:
        if isinstance(backend_obj, PodmanBackend):
            _upgrade_podman(name, backend_obj, True, overrides)
        else:
            typer.echo("Unsupported backend for upgrade.", err=True)
            raise typer.Exit(1)
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except typer.Exit:
        raise
    except KeyboardInterrupt:
        typer.echo("", err=True)
        typer.echo(
            f"Upgrade of '{name}' interrupted before it finished. "
            "Your workspace data is safe.",
            err=True,
        )
        typer.echo(
            f"Run 'paude upgrade {name}' again to finish the upgrade.",
            err=True,
        )
        raise typer.Exit(130) from None
    except Exception as e:
        typer.echo(f"Error upgrading session: {e}", err=True)
        typer.echo(
            f"The upgrade did not finish. Your workspace data is safe. "
            f"Run 'paude upgrade {name}' again to retry.",
            err=True,
        )
        raise typer.Exit(1) from None

    # Update registry and clear the upgrade manifest (success path only).
    # Re-probe: the container was rebuilt, so refresh from the new session.
    _finalize_upgrade(name, backend_obj.get_session(name))
    typer.echo(f"Session '{name}' upgraded to version {__version__}.")


@dataclass
class _ResolvedUpgrade:
    """Fully-resolved session configuration for a (re)build during upgrade.

    Sourced either from the old container's labels (fresh upgrade) or from a
    persisted :class:`~paude.cli.upgrade_state.UpgradeManifest` (resumed
    upgrade), then normalised through :func:`_apply_overrides`.
    """

    agent_name: str
    provider_name: str | None
    composition: AgentComposition
    credential_providers: list[str]
    workspace: Path
    gpu: str | None
    yolo: bool
    otel_endpoint: str | None
    allowed_domains: list[str] | None
    proxy_image_label: str | None


def _resolve_base_from_labels(
    runner: ContainerRunner, name: str, labels: dict[str, str]
) -> _ResolvedUpgrade:
    """Read the session's configuration from its container labels."""
    from paude.backends.labels import (
        PAUDE_LABEL_AGENT,
        PAUDE_LABEL_DOMAINS,
        PAUDE_LABEL_GPU,
        PAUDE_LABEL_OTEL_ENDPOINT,
        PAUDE_LABEL_PROVIDER,
        PAUDE_LABEL_PROXY_IMAGE,
        PAUDE_LABEL_WORKSPACE,
        PAUDE_LABEL_YOLO,
    )
    from paude.backends.podman.helpers import (
        get_session_composition,
        get_session_credential_providers,
    )
    from paude.backends.session_env import decode_path

    workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    workspace = (
        decode_path(workspace_encoded, url_safe=True)
        if workspace_encoded
        else Path.cwd()
    )

    # Domain config — old sessions may not have the label (no proxy).
    # On upgrade, all sessions get a proxy; default to None to trigger
    # expansion via _prepare_session_create (which defaults to ["default"]).
    domains_str = labels.get(PAUDE_LABEL_DOMAINS)
    allowed_domains: list[str] | None = None
    if domains_str is not None:
        allowed_domains = domains_str.split(",") if domains_str else []

    return _ResolvedUpgrade(
        agent_name=labels.get(PAUDE_LABEL_AGENT, "claude"),
        provider_name=labels.get(PAUDE_LABEL_PROVIDER),
        composition=get_session_composition(runner, name),
        credential_providers=get_session_credential_providers(runner, name),
        workspace=workspace,
        gpu=labels.get(PAUDE_LABEL_GPU),
        yolo=labels.get(PAUDE_LABEL_YOLO) == "1",
        otel_endpoint=labels.get(PAUDE_LABEL_OTEL_ENDPOINT),
        allowed_domains=allowed_domains,
        proxy_image_label=labels.get(PAUDE_LABEL_PROXY_IMAGE),
    )


def _resolve_base_from_manifest(manifest: UpgradeManifest) -> _ResolvedUpgrade:
    """Rebuild the session's configuration from a persisted upgrade manifest.

    Used when resuming an interrupted upgrade whose original container (the
    only other source of this config) has already been removed.
    """
    from paude.agents import get_agents

    specs = manifest.agent_providers or [(manifest.agent, manifest.provider or "")]
    composition = get_agents(
        [agent for agent, _provider in specs],
        providers={agent: provider for agent, provider in specs if provider},
        include_bundled=False,
    )
    return _ResolvedUpgrade(
        agent_name=manifest.agent,
        provider_name=manifest.provider,
        composition=composition,
        credential_providers=list(manifest.credential_providers or []),
        workspace=Path(manifest.workspace),
        gpu=manifest.gpu,
        yolo=manifest.yolo,
        otel_endpoint=manifest.otel_endpoint,
        allowed_domains=manifest.allowed_domains,
        proxy_image_label=manifest.proxy_image,
    )


def _apply_overrides(state: _ResolvedUpgrade, overrides: UpgradeOverrides) -> None:
    """Apply CLI overrides to a resolved config in place, normalising provider."""
    from paude.agents import get_agents

    if overrides.provider is not None:
        state.provider_name = overrides.provider
        specs = [
            (item.config.name, item.config.provider or "")
            for item in state.composition.agents
        ]
        specs[0] = (specs[0][0], state.provider_name)
        state.composition = get_agents(
            [agent for agent, _provider in specs],
            providers={agent: provider for agent, provider in specs if provider},
            include_bundled=False,
        )
    elif overrides.agent_providers is not None:
        installed = [item.config.name for item in state.composition.agents]
        unknown = [a for a in overrides.agent_providers if a not in installed]
        if unknown:
            raise ValueError(
                "Provider mappings reference agents that are not installed: "
                + ", ".join(unknown)
            )
        state.composition = get_agents(
            installed,
            providers=overrides.agent_providers,
            include_bundled=False,
        )

    mappings_changed = (
        overrides.provider is not None or overrides.agent_providers is not None
    )
    mapped_providers = list(
        dict.fromkeys(
            item.config.provider or ""
            for item in state.composition.agents
            if item.config.provider
        )
    )
    if overrides.providers is not None:
        from paude.providers import get_provider

        state.credential_providers = list(dict.fromkeys(overrides.providers))
        for provider in state.credential_providers:
            get_provider(provider)
        missing = [
            provider
            for provider in mapped_providers
            if provider not in state.credential_providers
        ]
        if missing:
            raise ValueError(
                "Credential providers must include every mapped provider; missing: "
                + ", ".join(missing)
            )
    elif mappings_changed:
        state.credential_providers = mapped_providers

    state.provider_name = state.composition.primary.config.provider
    if overrides.gpu is not None:
        state.gpu = overrides.gpu if overrides.gpu != "" else None
    if overrides.yolo is not None:
        state.yolo = overrides.yolo
    if overrides.otel_endpoint is not None:
        # Empty string means "remove OTEL".
        state.otel_endpoint = overrides.otel_endpoint or None
    if overrides.allowed_domains is not None:
        state.allowed_domains = overrides.allowed_domains


def _manifest_from_state(
    name: str,
    state: _ResolvedUpgrade,
    to_version: str,
    created_at: str,
) -> UpgradeManifest:
    """Capture a resolved config as a durable manifest for crash recovery."""
    from paude.upgrade_state import UpgradeManifest

    agent_specs = [
        (item.config.name, item.config.provider or "")
        for item in state.composition.agents
    ]
    return UpgradeManifest(
        name=name,
        to_version=to_version,
        created_at=created_at,
        workspace=str(state.workspace),
        agent=state.agent_name,
        provider=state.provider_name,
        agent_providers=agent_specs,
        credential_providers=list(state.credential_providers),
        gpu=state.gpu,
        yolo=state.yolo,
        otel_endpoint=state.otel_endpoint,
        allowed_domains=state.allowed_domains,
        proxy_image=state.proxy_image_label,
    )


def _upgrade_podman(
    name: str,
    backend: PodmanBackend,
    rebuild: bool,
    overrides: UpgradeOverrides,
) -> None:
    """Upgrade a Podman/Docker session in place.

    The whole flow is idempotent and resumable: the resolved configuration is
    written to a durable manifest before anything is torn down, teardown uses
    force/tolerant removals, and ``create_session(reuse_volume=True)`` rebuilds
    from a clean slate while preserving the workspace volume. Re-running after
    an interruption reads the manifest and converges to a single healthy
    session.
    """
    from datetime import UTC, datetime

    from paude import __version__, upgrade_state
    from paude.backends.podman.helpers import (
        container_name,
        find_container_by_session_name,
        network_name,
        proxy_container_name,
    )
    from paude.cli.helpers import (
        _detect_dev_script_dir,
        _prepare_session_create,
    )
    from paude.config.detector import detect_config
    from paude.config.parser import parse_config
    from paude.container import ImageManager
    from paude.mounts import build_mounts

    # Resolve the session config from a manifest (resume) or labels (fresh).
    manifest = upgrade_state.load(name)
    if manifest is not None:
        state = _resolve_base_from_manifest(manifest)
        created_at = manifest.created_at
    else:
        container = find_container_by_session_name(backend._runner, name)
        if container is None:
            typer.echo(f"Container for session '{name}' not found.", err=True)
            raise typer.Exit(1)
        labels = container.get("Labels", {}) or {}
        state = _resolve_base_from_labels(backend._runner, name, labels)
        created_at = datetime.now(UTC).isoformat()

    _apply_overrides(state, overrides)

    # Persist the fully-resolved config BEFORE any destructive step, so an
    # interrupt from here on can be finished by re-running the upgrade.
    upgrade_state.save(_manifest_from_state(name, state, __version__, created_at))

    composition = state.composition
    workspace = state.workspace

    # Detect project config from workspace
    config = None
    config_file = detect_config(workspace)
    if config_file:
        config = parse_config(config_file)

    engine = backend._engine
    agent_instance = composition.primary
    agent_specs = [
        (item.config.name, item.config.provider or "") for item in composition.agents
    ]

    # Salvage state written by older images before deleting their writable
    # layer. Skipped when resuming after the old container is already gone —
    # its state was already copied into the workspace volume.
    cname = container_name(name)
    if backend._runner.container_exists(cname):
        from paude.cli.upgrade_persistence import migrate_legacy_state

        typer.echo("Migrating persistent agent state...", err=True)
        migrate_legacy_state(backend._runner, cname, composition)

    image_manager = ImageManager(
        script_dir=_detect_dev_script_dir(),
        agent=agent_instance,
        composition=composition,
        engine=engine,
    )

    # A build failure here is transient and non-destructive: nothing has been
    # torn down yet and the manifest is already saved. Raise a plain exception
    # (not typer.Exit) so session_upgrade's generic handler reports it, tells
    # the user their data is safe, and points them at re-running to retry — the
    # bare `except typer.Exit: raise` path would swallow that guidance.
    try:
        if config is not None and config.has_customizations:
            image = image_manager.ensure_custom_image(
                config, force_rebuild=True, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image(force_rebuild=True)
    except Exception as e:
        raise RuntimeError(f"building the agent image failed: {e}") from e

    # Build proxy image (always required — all sessions use proxy)
    proxy_image: str | None = None
    try:
        proxy_image = image_manager.ensure_proxy_image(force_rebuild=True)
    except Exception as e:
        raise RuntimeError(f"building the proxy image failed: {e}") from e

    # Remove old container and proxy resources (but NOT the workspace volume).
    # All removals are force/tolerant, so re-running after a partial teardown
    # is safe.
    typer.echo(f"Removing old container {cname}...", err=True)
    backend._runner.remove_container(cname, force=True)

    pname = proxy_container_name(name)
    backend._runner.remove_container(pname, force=True)
    nname = network_name(name)
    backend._network_manager.remove_network(nname)

    # Remove CA volume so create_session can recreate it
    from paude.backends.podman.proxy import ca_volume_name

    backend._volume_manager.remove_volume(ca_volume_name(name), force=True)

    # Build mounts and env
    home = Path.home()
    mounts = build_mounts(home, composition, include_config=engine.is_remote)

    # Expand domains — all sessions get a proxy.
    # allowed_domains=None (old sessions without proxy) is passed as-is to
    # _prepare_session_create, which defaults to ["default"].
    expanded_domains, parsed_args, env, unrestricted = _prepare_session_create(
        state.allowed_domains,
        state.yolo,
        None,
        config,
        agent_name=state.agent_name,
        provider_name=state.provider_name,
        otel_endpoint=state.otel_endpoint,
        composition=composition,
        credential_providers=state.credential_providers,
    )
    session_domains = expanded_domains

    # Compute OTEL proxy ports
    otel_ports: list[int] = []
    if state.otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(state.otel_endpoint)

    # Create new session config with reuse_volume=True
    from paude.backends import SessionConfig

    session_config = SessionConfig(
        name=name,
        workspace=workspace,
        image=image,
        env=env,
        mounts=mounts,
        allowed_domains=session_domains,
        yolo=state.yolo,
        proxy_image=proxy_image or state.proxy_image_label,
        agent=state.agent_name,
        provider=state.provider_name,
        agent_providers=agent_specs,
        credential_providers=state.credential_providers,
        gpu=state.gpu,
        reuse_volume=True,
        ports=composition.exposed_ports,
        otel_ports=otel_ports,
        otel_endpoint=state.otel_endpoint,
    )

    backend.create_session(session_config)
    backend.start_session_no_attach(name)
