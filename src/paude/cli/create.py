"""Session create command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from paude.agents import get_agent
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _expand_allowed_domains,
    _parse_agent_args,
    _prepare_session_create,
    _split_list_option,
)


@app.command("create")
def session_create(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-generated if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = None,
    yolo: Annotated[
        bool | None,
        typer.Option(
            "--yolo/--no-yolo",
            help="Enable YOLO mode (skip all permission prompts).",
        ),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domains",
            help=(
                "Domains to allow network access. Can be repeated. "
                "Special values: 'all' (unrestricted), "
                "'default' (vertexai+python+github), "
                "'vertexai', 'python', 'golang', 'nodejs', "
                "'rust'. Default: 'default'."
            ),
        ),
    ] = None,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Force rebuild of workspace container image.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show configuration and what would be done, then exit.",
        ),
    ] = False,
    claude_args: Annotated[
        str | None,
        typer.Option(
            "--args",
            "-a",
            help="Arguments to pass to claude (e.g., -a '-p \"prompt\"').",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose output (affects --dry-run display).",
        ),
    ] = False,
    platform: Annotated[
        str | None,
        typer.Option(
            "--platform",
            help="Target platform for image builds (e.g., linux/amd64, linux/arm64).",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help=(
                "Agent to use: claude (default), codex, cursor, gascity, "
                "gemini, openclaw, opencode. Alias for a single-item --agents."
            ),
        ),
    ] = None,
    agents: Annotated[
        list[str] | None,
        typer.Option(
            "--agents",
            help=(
                "Agents to use (comma-separated and/or repeatable; first is "
                "primary), e.g. --agents gascity,claude,codex. Cannot be "
                "combined with --agent."
            ),
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help=(
                "Inference provider (e.g., vertex, openai, anthropic). "
                "Alias for a single-item --providers."
            ),
        ),
    ] = None,
    providers: Annotated[
        list[str] | None,
        typer.Option(
            "--providers",
            help=(
                "Inference providers (comma-separated and/or repeatable), "
                "e.g. --providers vertex,chatgpt. Cannot be combined with "
                "--provider."
            ),
        ),
    ] = None,
    git: Annotated[
        bool | None,
        typer.Option(
            "--git/--no-git",
            help="Set up git remote, push code+tags, configure origin.",
        ),
    ] = None,
    no_clone_origin: Annotated[
        bool,
        typer.Option(
            "--no-clone-origin",
            help="Skip cloning from origin in container (force full push).",
        ),
    ] = False,
    gpu: Annotated[
        str | None,
        typer.Option(
            "--gpu",
            help=(
                "Pass GPU devices to the container. "
                "Use --gpu without a value for all GPUs, "
                "or --gpu=device=0,1 for specific devices."
            ),
        ),
    ] = None,
    no_gpu: Annotated[
        bool,
        typer.Option(
            "--no-gpu",
            help="Explicitly disable GPU passthrough (overrides user defaults).",
        ),
    ] = False,
    otel_endpoint: Annotated[
        str | None,
        typer.Option(
            "--otel-endpoint",
            help="OTLP collector endpoint for telemetry export (e.g., http://collector:4318).",
        ),
    ] = None,
    forward_port: Annotated[
        list[str] | None,
        typer.Option(
            "--forward-port",
            help=(
                "Forward a container port to the host. Repeatable. Accepts "
                "PORT (same on both), HOST:CONTAINER, or HOST_IP:HOST:CONTAINER. "
                "Binds 127.0.0.1 by default."
            ),
        ),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="Remote host for container execution (user@hostname[:port]).",
        ),
    ] = None,
    ssh_key: Annotated[
        str | None,
        typer.Option(
            "--ssh-key",
            help="SSH private key path for remote host.",
        ),
    ] = None,
) -> None:
    """Create a new persistent session (does not start it)."""
    from paude.config import detect_config, parse_config
    from paude.config.resolver import resolve_create_options
    from paude.config.user_config import load_user_defaults

    workspace = Path.cwd()

    # Load user defaults
    user_defaults = load_user_defaults()

    # Detect and parse project config
    config_file = detect_config(workspace)
    config = None
    if config_file:
        try:
            config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Error parsing config: {e}", err=True)
            raise typer.Exit(1) from None

    # Resolve --gpu / --no-gpu: --no-gpu disables (even if user default is set)
    cli_gpu: str | None = gpu
    if no_gpu:
        cli_gpu = ""  # empty string sentinel = explicitly disabled

    # Normalize repeatable, comma-separated list options.
    cli_agents = _split_list_option(agents)
    cli_providers = _split_list_option(providers)

    # Resolve layered configuration
    try:
        resolved = resolve_create_options(
            cli_backend=backend.value if backend is not None else None,
            cli_agent=agent,
            cli_provider=provider,
            cli_agents=cli_agents,
            cli_providers=cli_providers,
            cli_yolo=yolo,
            cli_git=git,
            cli_platform=platform,
            cli_gpu=cli_gpu,
            cli_allowed_domains=allowed_domains,
            cli_otel_endpoint=otel_endpoint,
            cli_forward_ports=forward_port,
            project_config=config,
            user_defaults=user_defaults,
        )
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Extract resolved values
    r_backend = BackendType(resolved.backend.value)
    r_agent = resolved.agent.value
    r_provider = resolved.provider.value
    r_yolo = resolved.yolo.value
    r_git = resolved.git.value
    r_platform = resolved.platform.value
    # Empty string means explicitly disabled via --no-gpu
    r_gpu = resolved.gpu.value or None
    r_otel_endpoint = resolved.otel_endpoint.value

    # Parse forwarded port specs into (host_ip, host_port, container_port) tuples
    from paude.backends.port_forward_utils import parse_forward_port_specs

    try:
        r_forward_ports = parse_forward_port_specs(resolved.forward_ports.value)
    except ValueError as e:
        typer.echo(
            f"Error: {e} (from {resolved.forward_ports.source})",
            err=True,
        )
        raise typer.Exit(1) from None

    # Use resolved domains, or fall back to ["default"] if nothing configured
    r_allowed_domains: list[str] | None = (
        resolved.allowed_domains if resolved.allowed_domains else None
    )

    # Validate agent name and provider combination
    try:
        get_agent(r_agent, provider=r_provider)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Handle dry-run mode
    if dry_run:
        from paude.cli.helpers import _get_provider_aliases
        from paude.dry_run import show_dry_run

        parsed_args = _parse_agent_args(claude_args)
        agent_instance = get_agent(r_agent, provider=r_provider)

        expanded = _expand_allowed_domains(
            r_allowed_domains,
            extra_aliases=agent_instance.config.extra_domain_aliases,
            provider_aliases=_get_provider_aliases(r_provider, r_agent),
            required_aliases=agent_instance.config.required_domain_aliases,
        )
        show_dry_run(
            flags={
                "allowed_domains": expanded,
                "rebuild": rebuild,
                "verbose": verbose,
                "claude_args": parsed_args,
            },
            resolved=resolved,
        )
        raise typer.Exit()

    # Multi-agent creation is not yet supported: a real (non-dry-run) create
    # launches only the primary agent. Warn so extra agents aren't dropped
    # silently -- use --dry-run to preview the full multi-agent resolution.
    if len(resolved.agents) > 1:
        dropped = ", ".join(resolved.agents[1:])
        typer.echo(
            "Warning: multi-agent creation is not yet supported; creating only "
            f"the primary agent '{r_agent}'. Ignoring: {dropped}. "
            "Use --dry-run to preview the full multi-agent resolution.",
            err=True,
        )

    if resolved.dropped_providers:
        dropped_providers = ", ".join(resolved.dropped_providers)
        typer.echo(
            "Warning: some --providers entries are not used by any agent; "
            f"ignoring: {dropped_providers}.",
            err=True,
        )

    if ssh_key and not host:
        typer.echo(
            "Error: --ssh-key requires --host.",
            err=True,
        )
        raise typer.Exit(1)

    # Build SSH transport if --host is specified
    ssh_transport = None
    parsed_ssh_host: str | None = None
    ssh_port: int | None = None
    if host:
        from paude.transport.ssh import SshTransport, parse_ssh_host

        parsed_ssh_host, ssh_port = parse_ssh_host(host)
        ssh_transport = SshTransport(parsed_ssh_host, key=ssh_key, port=ssh_port)
        try:
            typer.echo(
                f"Validating SSH connection to {parsed_ssh_host}...",
                err=True,
            )
            ssh_transport.validate()
            ssh_transport.validate_engine(r_backend.value)
        except RuntimeError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None

    # Shared pre-create: parse args, build env, expand domains, show warnings
    expanded_domains, parsed_args, env, unrestricted = _prepare_session_create(
        allowed_domains=r_allowed_domains,
        yolo=r_yolo,
        claude_args=claude_args,
        config_obj=config,
        agent_name=r_agent,
        provider_name=r_provider,
        otel_endpoint=r_otel_endpoint,
    )

    # Compute OTEL proxy ports (non-standard ports to allow through proxy)
    otel_ports: list[int] = []
    if r_otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(r_otel_endpoint)

    from paude.cli.create_podman import create_podman_session

    create_podman_session(
        name=name,
        workspace=workspace,
        config=config,
        env=env,
        expanded_domains=expanded_domains,
        unrestricted=unrestricted,
        parsed_args=parsed_args,
        yolo=r_yolo,
        git=r_git,
        no_clone_origin=no_clone_origin,
        rebuild=rebuild,
        platform=r_platform,
        agent_name=r_agent,
        provider_name=r_provider,
        engine_binary=r_backend.value,
        ssh_host=parsed_ssh_host,
        ssh_key=ssh_key,
        transport=ssh_transport,
        gpu=r_gpu,
        otel_ports=otel_ports,
        otel_endpoint=r_otel_endpoint,
        forward_ports=r_forward_ports,
    )
