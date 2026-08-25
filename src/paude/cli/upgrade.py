"""Upgrade command for paude sessions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.labels import SessionSpec
from paude.backends.podman.helpers import (
    agent_specs_for,
    composition_for_spec,
    credential_providers_for_spec,
)
from paude.cli.app import BackendType, app
from paude.cli.helpers import find_session_backend
from paude.subprocess_utils import called_process_stderr

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.backends.base import Session
    from paude.backends.labels import LabeledSession
    from paude.backends.podman.backend import PodmanBackend
    from paude.cli.session_rebuild import SessionImages
    from paude.config.models import PaudeConfig
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
    add_agents: list[str] | None = None  # additive: append, keep primary
    agents: list[str] | None = None  # full-set replacement (first = primary)

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
            or self.add_agents is not None
            or self.agents is not None
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
    add_agent: Annotated[
        list[str] | None,
        typer.Option(
            "--add-agent",
            help=(
                "Add one or more agents to the session (comma-separated and/or "
                "repeatable), e.g. --add-agent codex. The primary agent is "
                "unchanged; already-present agents are ignored."
            ),
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Redefine the session's single agent (alias for --agents X).",
        ),
    ] = None,
    agents: Annotated[
        list[str] | None,
        typer.Option(
            "--agents",
            help=(
                "Redefine the full agent set (comma-separated and/or repeatable); "
                "the first is primary. Must include every installed agent "
                "(removing agents is not yet supported)."
            ),
        ),
    ] = None,
) -> None:
    """Upgrade a session to the current paude version.

    Can also reconfigure session options (e.g., --otel-endpoint, --gpu) and add
    agents (--add-agent, --agents) without losing workspace or agent data.
    Upgrades always pull and rebuild agent tooling, including when the Paude
    version is unchanged.
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

    # Resolve the agent-set options. --agent is a one-item alias for --agents.
    cli_add_agents = _split_list_option(add_agent)
    if agent is not None and agents is not None:
        typer.echo("Error: Specify --agent or --agents, not both.", err=True)
        raise typer.Exit(1)
    cli_agents = _split_list_option([agent] if agent is not None else agents)
    if cli_add_agents is not None and cli_agents is not None:
        typer.echo(
            "Error: Specify --add-agent or --agent/--agents, not both.", err=True
        )
        raise typer.Exit(1)

    overrides = UpgradeOverrides(
        otel_endpoint=otel_endpoint,
        allowed_domains=allowed_domains,
        gpu=cli_gpu,
        yolo=cli_yolo,
        provider=provider,
        providers=_split_list_option(providers),
        agent_providers=cli_agent_providers,
        add_agents=cli_add_agents,
        agents=cli_agents,
    )

    # Validate agent/provider names up front, before finding the session or
    # stopping a running container, so a typo (e.g. --add-agent coddex) fails
    # fast instead of tearing down the session and leaving it stopped with no
    # manifest to resume. Reuse the registry lookups so the error text matches
    # the deeper validation. Agent/provider *compatibility* is still checked
    # later, once the composition is resolved.
    from paude.agents import get_agent
    from paude.providers import get_provider

    try:
        for agent_name in (cli_add_agents or []) + (cli_agents or []):
            get_agent(agent_name)
        provider_names = list(overrides.providers or [])
        if provider is not None:
            provider_names.append(provider)
        if cli_agent_providers is not None:
            provider_names.extend(cli_agent_providers.values())
        for provider_name in provider_names:
            get_provider(provider_name)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

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
        # str(CalledProcessError) omits captured stderr — surface it so a
        # failing container command isn't reported as an opaque exit status.
        detail = called_process_stderr(e) or str(e)
        typer.echo(f"Error upgrading session: {detail}", err=True)
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
class ResolvedSession:
    """A session's configuration, fully resolved and ready to rebuild from.

    Sourced either from the old container's labels (fresh upgrade) or from a
    persisted :class:`~paude.upgrade_state.UpgradeManifest` (resumed upgrade),
    then normalised through :func:`_apply_overrides`.

    The declared configuration lives in ``spec``, shared with both manifests,
    so it is copied rather than restated. The other two fields are what a spec
    deliberately excludes: ``composition`` is not serializable, and
    ``workspace`` has no single sensible default across callers.
    """

    spec: SessionSpec
    composition: AgentComposition
    workspace: Path


def _resolve_base_from_view(view: LabeledSession) -> ResolvedSession:
    """Read the session's configuration from its container labels."""
    return ResolvedSession(
        # Copied, not aliased: LabeledSession is frozen but its spec is not,
        # and _apply_overrides mutates what it is handed. Credential providers
        # are derived rather than read raw, so a session created before the
        # providers label existed keeps the set its agents imply.
        spec=replace(
            view.spec,
            credential_providers=credential_providers_for_spec(view.spec),
        ),
        composition=composition_for_spec(view.spec),
        # A session created before the workspace label existed: the current
        # directory is the only guess available, and it is what upgrade has
        # always used.
        workspace=view.workspace or Path.cwd(),
    )


def _resolve_base_from_manifest(manifest: UpgradeManifest) -> ResolvedSession:
    """Rebuild the session's configuration from a persisted upgrade manifest.

    Used when resuming an interrupted upgrade whose original container (the
    only other source of this config) has already been removed.
    """
    spec = SessionSpec(
        agent=manifest.agent,
        provider=manifest.provider,
        agent_providers=list(manifest.agent_providers),
        credential_providers=list(manifest.credential_providers or []),
        gpu=manifest.gpu,
        yolo=manifest.yolo,
        otel_endpoint=manifest.otel_endpoint,
        allowed_domains=manifest.allowed_domains,
        proxy_image=manifest.proxy_image,
    )
    return ResolvedSession(
        spec=spec,
        composition=composition_for_spec(spec),
        workspace=Path(manifest.workspace),
    )


def _apply_overrides(state: ResolvedSession, overrides: UpgradeOverrides) -> None:
    """Apply CLI overrides to a resolved config in place, normalising provider.

    Agent-set changes (``--add-agent`` / ``--agents``) and provider remappings
    (``--provider`` / ``--agent-provider``) funnel through a single composition
    rebuild: a target name list is derived first, then a provider mapping seeded
    from the existing per-agent providers and overlaid with CLI overrides, then
    :func:`~paude.config.resolver._derive_agent_providers` validates and fills
    defaults for any newly-added agent.
    """
    from paude.agents import get_agents
    from paude.config.resolver import _derive_agent_providers

    existing_specs = [
        (item.config.name, item.config.provider or "")
        for item in state.composition.agents
    ]
    existing_names = [name for name, _provider in existing_specs]

    if overrides.agents is not None:
        # Full-set redefinition (first = primary). Removal is not yet supported,
        # so the new set must retain every currently-installed agent.
        target_names = list(dict.fromkeys(overrides.agents))
        dropped = [name for name in existing_names if name not in target_names]
        if dropped:
            raise ValueError(
                "Removing agents is not yet supported. --agents must include all "
                f"installed agents ({', '.join(existing_names)}). "
                "Use --add-agent to add agents."
            )
    elif overrides.add_agents:
        # Additive: preserve the existing order (and primary), append new agents.
        target_names = list(existing_names)
        for agent in overrides.add_agents:
            if agent not in target_names:
                target_names.append(agent)
    else:
        target_names = list(existing_names)

    # Seed the provider mapping from current per-agent providers, then overlay
    # CLI overrides so existing agents keep their resolved provider unless the
    # user remaps them. Every existing agent is always in target_names (add
    # appends to them; replace must retain them), so no membership filter is
    # needed here.
    mappings = {name: provider for name, provider in existing_specs if provider}
    if overrides.provider is not None:
        mappings[target_names[0]] = overrides.provider
    if overrides.agent_providers is not None:
        mappings.update(overrides.agent_providers)

    remap = overrides.provider is not None or overrides.agent_providers is not None
    agent_set_changed = overrides.agents is not None or bool(overrides.add_agents)
    if remap or agent_set_changed:
        # _derive_agent_providers validates every agent name and agent/provider
        # combination, fills unmapped (e.g. newly-added) agents with their
        # DEFAULT_PROVIDER, and raises "not installed" only for mappings that
        # reference agents outside target_names — so a legitimately-added agent
        # is accepted, while --agent-provider codex=... without --add-agent still
        # correctly rejects codex.
        agent_providers = _derive_agent_providers(target_names, mappings)
        state.composition = get_agents(
            target_names,
            providers=dict(agent_providers),
            include_bundled=False,
        )

    mapped_providers = list(
        dict.fromkeys(
            item.config.provider
            for item in state.composition.agents
            if item.config.provider
        )
    )
    if overrides.providers is not None:
        from paude.providers import get_provider

        state.spec.credential_providers = list(dict.fromkeys(overrides.providers))
        for provider in state.spec.credential_providers:
            get_provider(provider)
        missing = [
            provider
            for provider in mapped_providers
            if provider not in state.spec.credential_providers
        ]
        if missing:
            raise ValueError(
                "Credential providers must include every mapped provider; missing: "
                + ", ".join(missing)
            )
    elif agent_set_changed:
        # Agent-set change (add, or reorder/redefine), possibly combined with a
        # remap: union the composition's providers onto the existing credential
        # set so a previously-provisioned provider is preserved. Checked before
        # `remap` so the documented "add + set provider" workflow
        # (--add-agent X --agent-provider X=P) does not drop credential-only
        # providers.
        state.spec.credential_providers = list(
            dict.fromkeys([*state.spec.credential_providers, *mapped_providers])
        )
    elif remap:
        # Pure remap (no agent-set change): replace the credential set with the
        # mapped set, dropping any provider no longer referenced by an agent.
        state.spec.credential_providers = mapped_providers

    # Keep the primary-agent scalars in sync with the (possibly reordered or
    # extended) composition, so a changed primary is reflected in labels/env.
    state.spec.agent = state.composition.primary.config.name
    state.spec.provider = state.composition.primary.config.provider
    state.spec.agent_providers = agent_specs_for(state.composition)
    if overrides.gpu is not None:
        state.spec.gpu = overrides.gpu if overrides.gpu != "" else None
    if overrides.yolo is not None:
        state.spec.yolo = overrides.yolo
    if overrides.otel_endpoint is not None:
        # Empty string means "remove OTEL".
        state.spec.otel_endpoint = overrides.otel_endpoint or None
    if overrides.allowed_domains is not None:
        state.spec.allowed_domains = overrides.allowed_domains


def _manifest_from_state(
    name: str,
    state: ResolvedSession,
    to_version: str,
    created_at: str,
) -> UpgradeManifest:
    """Capture a resolved config as a durable manifest for crash recovery."""
    from paude.upgrade_state import UpgradeManifest

    return UpgradeManifest(
        name=name,
        to_version=to_version,
        created_at=created_at,
        workspace=str(state.workspace),
        agent=state.spec.agent,
        provider=state.spec.provider,
        agent_providers=list(state.spec.agent_providers),
        credential_providers=list(state.spec.credential_providers),
        gpu=state.spec.gpu,
        yolo=state.spec.yolo,
        otel_endpoint=state.spec.otel_endpoint,
        allowed_domains=state.spec.allowed_domains,
        proxy_image=state.spec.proxy_image,
    )


def _upgrade_podman(
    name: str,
    backend: PodmanBackend,
    rebuild: bool,
    overrides: UpgradeOverrides,
) -> None:
    """Upgrade a Podman/Docker session in place.

    The whole flow is idempotent and resumable: the resolved configuration is
    written to a durable manifest before anything is torn down, the images are
    built before that teardown so a build failure changes nothing, teardown
    uses force/tolerant removals, and ``create_session(reuse_volume=True)``
    rebuilds from a clean slate while preserving the workspace volume.
    Re-running after an interruption reads the manifest and converges to a
    single healthy session.
    """
    from paude import __version__, upgrade_state
    from paude.cli.session_rebuild import ImageBuildError, build_session_images
    from paude.config.detector import detect_config
    from paude.config.parser import parse_config

    state, created_at = _resolve_upgrade_state(name, backend)
    _apply_overrides(state, overrides)

    # Persist the fully-resolved config BEFORE any destructive step, so an
    # interrupt from here on can be finished by re-running the upgrade.
    upgrade_state.save(_manifest_from_state(name, state, __version__, created_at))

    config = None
    config_file = detect_config(state.workspace)
    if config_file:
        config = parse_config(config_file)

    # Salvage state written by older images before deleting their writable
    # layer. A no-op when resuming after the old container is already gone —
    # its state was already copied into the workspace volume.
    backend.resources.migrate_legacy_state(name, state.composition)

    # A build failure here is transient and non-destructive: nothing has been
    # torn down yet and the manifest is already saved. Re-raise as a plain
    # exception (not typer.Exit) so session_upgrade's generic handler reports
    # it, tells the user their data is safe, and points them at re-running to
    # retry — the bare `except typer.Exit: raise` path would swallow that.
    try:
        images = build_session_images(
            engine=backend.engine,
            composition=state.composition,
            config=config,
            workspace=state.workspace,
            force_rebuild=True,
        )
    except ImageBuildError as e:
        raise RuntimeError(str(e)) from e

    # Only now, with both images in hand, is anything removed. Deliberately
    # keeps the workspace volume, the proxy auth volume and the credential
    # secrets — see SessionResources.teardown_for_rebuild.
    backend.resources.teardown_for_rebuild(name)

    _recreate_session(name, backend, state, images, config)


def _recreate_session(
    name: str,
    backend: PodmanBackend,
    state: ResolvedSession,
    images: SessionImages,
    config: PaudeConfig | None,
) -> None:
    """Build the replacement container around the preserved volume, and start it."""
    from paude.cli.helpers import _prepare_session_create
    from paude.cli.session_rebuild import (
        prepare_session_mounts,
        session_config_from_spec,
    )

    prepared = prepare_session_mounts(
        engine=backend.engine, composition=state.composition
    )

    # Expand domains — all sessions get a proxy. allowed_domains=None (an old
    # session created before that was true) is passed through as-is to
    # _prepare_session_create, which defaults it to ["default"].
    expanded_domains, _args, env, _unrestricted = _prepare_session_create(
        state.spec.allowed_domains,
        state.spec.yolo,
        None,
        config,
        agent_name=state.spec.agent,
        provider_name=state.spec.provider,
        otel_endpoint=state.spec.otel_endpoint,
        composition=state.composition,
        credential_providers=state.spec.credential_providers,
    )

    otel_ports: list[int] = []
    if state.spec.otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(state.spec.otel_endpoint)

    backend.create_session(
        session_config_from_spec(
            state.spec,
            name=name,
            workspace=state.workspace,
            composition=state.composition,
            images=images,
            env=env,
            mounts=prepared.mounts,
            expanded_domains=expanded_domains,
            otel_ports=otel_ports,
            reuse_volume=True,
        )
    )
    backend.start_session_no_attach(name)


def _resolve_upgrade_state(
    name: str, backend: PodmanBackend
) -> tuple[ResolvedSession, str]:
    """Resolve the session's configuration, and when this upgrade started.

    A leftover manifest means a previous attempt was interrupted: it is the
    only remaining source of the config once the old container is gone, so it
    wins over the container's labels and keeps the original start time.
    """
    from datetime import UTC, datetime

    from paude import upgrade_state

    manifest = upgrade_state.load(name)
    if manifest is not None:
        return _resolve_base_from_manifest(manifest), manifest.created_at

    view = backend.resources.labels(name)
    if view is None:
        typer.echo(f"Container for session '{name}' not found.", err=True)
        raise typer.Exit(1)
    return _resolve_base_from_view(view), datetime.now(UTC).isoformat()
