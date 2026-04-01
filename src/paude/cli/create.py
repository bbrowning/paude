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
    pvc_size: Annotated[
        str | None,
        typer.Option(
            "--pvc-size",
            help="PVC size for OpenShift (e.g., 10Gi).",
        ),
    ] = None,
    storage_class: Annotated[
        str | None,
        typer.Option(
            "--storage-class",
            help="Storage class for OpenShift.",
        ),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
    platform: Annotated[
        str | None,
        typer.Option(
            "--platform",
            help="Target platform for image builds (e.g., linux/amd64, linux/arm64).",
        ),
    ] = None,
    credential_timeout: Annotated[
        int | None,
        typer.Option(
            "--credential-timeout",
            help="Inactivity minutes before removing credentials (OpenShift).",
        ),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option(
            "--agent",
            help="Agent to use: claude (default), cursor, gemini, openclaw.",
        ),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Inference provider (e.g., vertex, openai, anthropic).",
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

    # Resolve layered configuration
    resolved = resolve_create_options(
        cli_backend=backend.value if backend is not None else None,
        cli_agent=agent,
        cli_provider=provider,
        cli_yolo=yolo,
        cli_git=git,
        cli_pvc_size=pvc_size,
        cli_credential_timeout=credential_timeout,
        cli_platform=platform,
        cli_openshift_context=openshift_context,
        cli_openshift_namespace=openshift_namespace,
        cli_gpu=cli_gpu,
        cli_allowed_domains=allowed_domains,
        cli_otel_endpoint=otel_endpoint,
        project_config=config,
        user_defaults=user_defaults,
    )

    # Extract resolved values
    r_backend = BackendType(resolved.backend.value)
    r_agent = resolved.agent.value
    r_provider = resolved.provider.value
    r_yolo = resolved.yolo.value
    r_git = resolved.git.value
    r_pvc_size = resolved.pvc_size.value
    r_credential_timeout = resolved.credential_timeout.value
    r_platform = resolved.platform.value
    r_openshift_context = resolved.openshift_context.value
    r_openshift_namespace = resolved.openshift_namespace.value
    # Empty string means explicitly disabled via --no-gpu
    r_gpu = resolved.gpu.value or None
    r_openshift_resources = resolved.openshift_resources.value
    r_openshift_build_resources = resolved.openshift_build_resources.value
    r_otel_endpoint = resolved.otel_endpoint.value

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

    # Validate --host
    if host and r_backend == BackendType.openshift:
        typer.echo(
            "Error: --host is not supported with --backend openshift.",
            err=True,
        )
        raise typer.Exit(1)

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
    expanded_domains, parsed_args, env, unrestricted, secret_env_mapping = (
        _prepare_session_create(
            allowed_domains=r_allowed_domains,
            yolo=r_yolo,
            claude_args=claude_args,
            config_obj=config,
            agent_name=r_agent,
            provider_name=r_provider,
            otel_endpoint=r_otel_endpoint,
        )
    )

    # Compute OTEL proxy ports (non-standard ports to open in squid)
    otel_ports: list[int] = []
    if r_otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(r_otel_endpoint)

    if r_backend in (BackendType.podman, BackendType.docker):
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
            secret_env_mapping=secret_env_mapping,
        )
    else:
        from paude.cli.create_openshift import create_openshift_session

        create_openshift_session(
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
            pvc_size=r_pvc_size,
            storage_class=storage_class,
            openshift_context=r_openshift_context,
            openshift_namespace=r_openshift_namespace,
            credential_timeout=r_credential_timeout,
            agent_name=r_agent,
            provider_name=r_provider,
            gpu=r_gpu,
            resources=r_openshift_resources,
            build_resources=r_openshift_build_resources,
            otel_ports=otel_ports,
            otel_endpoint=r_otel_endpoint,
            secret_env_mapping=secret_env_mapping,
        )
