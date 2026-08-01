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
    from paude.backends.podman.backend import PodmanBackend


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


@app.command("upgrade")
def session_upgrade(
    name: Annotated[str, typer.Argument(help="Session name to upgrade")],
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Force image rebuild even at same version."),
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
    without losing workspace data. Use --rebuild to force an image rebuild
    when only changing configuration at the same version.
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

    # Find session backend
    if backend is not None:
        backend_obj = _get_backend_instance(backend)
    else:
        result = find_session_backend(name)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        backend, backend_obj = result

    # Get session
    session = backend_obj.get_session(name)
    if session is None:
        typer.echo(f"Session '{name}' not found.", err=True)
        raise typer.Exit(1)

    has_overrides = overrides.has_changes()

    # Check version
    if session.version == __version__ and not rebuild and not has_overrides:
        typer.echo(
            f"Session '{name}' is already at version {__version__}. "
            "Use --rebuild to force an image rebuild, or pass config "
            "flags (e.g. --otel-endpoint) to reconfigure."
        )
        return

    if has_overrides and not rebuild and session.version == __version__:
        typer.echo(
            f"Reconfiguring session '{name}' (version {__version__})...",
            err=True,
        )
    else:
        old_version = session.version or "unknown"
        typer.echo(
            f"Upgrading session '{name}' from {old_version} to {__version__}...",
            err=True,
        )

    # Auto-stop if running
    if session.status == "running":
        typer.echo(f"Stopping session '{name}'...", err=True)
        backend_obj.stop_session(name)

    try:
        if isinstance(backend_obj, PodmanBackend):
            _upgrade_podman(name, backend_obj, rebuild, overrides)
        else:
            typer.echo("Unsupported backend for upgrade.", err=True)
            raise typer.Exit(1)
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error upgrading session: {e}", err=True)
        raise typer.Exit(1) from None

    # Update registry
    from paude.registry import SessionRegistry

    registry = SessionRegistry()
    entries = registry.load()
    if name in entries:
        refreshed = backend_obj.get_session(name)
        if refreshed is not None:
            entries[name].agent = refreshed.agent
            entries[name].agent_providers = refreshed.agent_providers
            entries[name].credential_providers = refreshed.credential_providers
        entries[name].paude_version = __version__
        registry._save(entries)

    typer.echo(f"Session '{name}' upgraded to version {__version__}.")


def _upgrade_podman(
    name: str,
    backend: PodmanBackend,
    rebuild: bool,
    overrides: UpgradeOverrides,
) -> None:
    """Upgrade a Podman/Docker session in place."""
    from paude.agents import get_agents
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
        container_name,
        find_container_by_session_name,
        get_session_composition,
        get_session_credential_providers,
        network_name,
        proxy_container_name,
    )
    from paude.backends.session_env import decode_path
    from paude.cli.helpers import (
        _detect_dev_script_dir,
        _prepare_session_create,
    )
    from paude.config.detector import detect_config
    from paude.config.parser import parse_config
    from paude.container import ImageManager
    from paude.mounts import build_mounts

    # Get container and extract labels
    container = find_container_by_session_name(backend._runner, name)
    if container is None:
        typer.echo(f"Container for session '{name}' not found.", err=True)
        raise typer.Exit(1)

    labels = container.get("Labels", {}) or {}

    agent_name = labels.get(PAUDE_LABEL_AGENT, "claude")
    provider_name = labels.get(PAUDE_LABEL_PROVIDER)
    composition = get_session_composition(backend._runner, name)
    credential_providers = get_session_credential_providers(backend._runner, name)
    workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    workspace = (
        decode_path(workspace_encoded, url_safe=True)
        if workspace_encoded
        else Path.cwd()
    )
    gpu = labels.get(PAUDE_LABEL_GPU)
    yolo = labels.get(PAUDE_LABEL_YOLO) == "1"
    otel_endpoint = labels.get(PAUDE_LABEL_OTEL_ENDPOINT)

    # Domain config — old sessions may not have the label (no proxy).
    # On upgrade, all sessions get a proxy; default to None to trigger
    # expansion via _prepare_session_create (which defaults to ["default"]).
    domains_str = labels.get(PAUDE_LABEL_DOMAINS)
    allowed_domains: list[str] | None = None
    if domains_str is not None:
        allowed_domains = domains_str.split(",") if domains_str else []

    proxy_image_label = labels.get(PAUDE_LABEL_PROXY_IMAGE)

    # Apply CLI overrides
    if overrides.provider is not None:
        provider_name = overrides.provider
        specs = [
            (item.config.name, item.config.provider or "")
            for item in composition.agents
        ]
        specs[0] = (specs[0][0], provider_name)
        composition = get_agents(
            [agent for agent, _provider in specs],
            providers={agent: provider for agent, provider in specs if provider},
            include_bundled=False,
        )
    elif overrides.agent_providers is not None:
        installed = [item.config.name for item in composition.agents]
        unknown = [name for name in overrides.agent_providers if name not in installed]
        if unknown:
            raise ValueError(
                "Provider mappings reference agents that are not installed: "
                + ", ".join(unknown)
            )
        composition = get_agents(
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
            for item in composition.agents
            if item.config.provider
        )
    )
    if overrides.providers is not None:
        from paude.providers import get_provider

        credential_providers = list(dict.fromkeys(overrides.providers))
        for provider in credential_providers:
            get_provider(provider)
        missing = [
            provider
            for provider in mapped_providers
            if provider not in credential_providers
        ]
        if missing:
            raise ValueError(
                "Credential providers must include every mapped provider; missing: "
                + ", ".join(missing)
            )
    elif mappings_changed:
        credential_providers = mapped_providers
    provider_name = composition.primary.config.provider
    if overrides.gpu is not None:
        gpu = overrides.gpu if overrides.gpu != "" else None
    if overrides.yolo is not None:
        yolo = overrides.yolo
    if overrides.otel_endpoint is not None:
        # Empty string means "remove OTEL"
        otel_endpoint = overrides.otel_endpoint if overrides.otel_endpoint else None
    if overrides.allowed_domains is not None:
        allowed_domains = overrides.allowed_domains

    # Detect project config from workspace
    config = None
    config_file = detect_config(workspace)
    if config_file:
        config = parse_config(config_file)

    # Build new image
    engine = backend._engine
    agent_instance = composition.primary
    agent_specs = [
        (item.config.name, item.config.provider or "") for item in composition.agents
    ]
    image_manager = ImageManager(
        script_dir=_detect_dev_script_dir(),
        agent=agent_instance,
        composition=composition,
        engine=engine,
    )

    try:
        if config is not None and config.has_customizations:
            image = image_manager.ensure_custom_image(
                config, force_rebuild=rebuild, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image(force_rebuild=rebuild)
    except Exception as e:
        typer.echo(f"Error building image: {e}", err=True)
        raise typer.Exit(1) from None

    # Build proxy image (always required — all sessions use proxy)
    proxy_image: str | None = None
    try:
        proxy_image = image_manager.ensure_proxy_image(force_rebuild=rebuild)
    except Exception as e:
        typer.echo(f"Error building proxy image: {e}", err=True)
        raise typer.Exit(1) from None

    # Remove old container and proxy resources (but NOT the volume)
    cname = container_name(name)
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
        allowed_domains,
        yolo,
        None,
        config,
        agent_name=agent_name,
        provider_name=provider_name,
        otel_endpoint=otel_endpoint,
        composition=composition,
        credential_providers=credential_providers,
    )
    session_domains = expanded_domains

    # Compute OTEL proxy ports
    otel_ports: list[int] = []
    if otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(otel_endpoint)

    # Create new session config with reuse_volume=True
    from paude.backends import SessionConfig

    session_config = SessionConfig(
        name=name,
        workspace=workspace,
        image=image,
        env=env,
        mounts=mounts,
        allowed_domains=session_domains,
        yolo=yolo,
        proxy_image=proxy_image or proxy_image_label,
        agent=agent_name,
        provider=provider_name,
        agent_providers=agent_specs,
        credential_providers=credential_providers,
        gpu=gpu,
        reuse_volume=True,
        ports=composition.exposed_ports,
        otel_ports=otel_ports,
        otel_endpoint=otel_endpoint,
    )

    backend.create_session(session_config)
    backend.start_session_no_attach(name)
