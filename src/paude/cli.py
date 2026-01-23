"""Typer CLI for paude."""

from __future__ import annotations

import os
from enum import Enum
from typing import Annotated

import typer

from paude import __version__

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated Podman container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)


class BackendType(str, Enum):
    """Container backend types."""

    podman = "podman"
    openshift = "openshift"


def version_callback(value: bool) -> None:
    """Print version information and exit."""
    if value:
        typer.echo(f"paude {__version__}")
        dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        registry = os.environ.get("PAUDE_REGISTRY", "docker.io/bbrowning")
        if dev_mode:
            typer.echo("  mode: development (PAUDE_DEV=1, building locally)")
        else:
            typer.echo(f"  mode: installed (pulling from {registry})")
        raise typer.Exit()


def show_help() -> None:
    """Show custom help message matching bash format."""
    help_text = """paude - Run Claude Code in a secure Podman container

USAGE:
    paude [OPTIONS] [-- CLAUDE_ARGS...]
    paude <COMMAND> [OPTIONS]

COMMANDS:
    sessions            List active sessions (OpenShift backend)
    attach              Attach to an existing session
    stop                Stop a session and clean up resources
    sync                Sync files between local and remote workspace

OPTIONS:
    -h, --help          Show this help message and exit
    -V, --version       Show paude version and exit
    --yolo              Enable YOLO mode (skip all permission prompts)
                        Claude can edit files and run commands without confirmation
    --allow-network     Allow unrestricted network access
                        By default, network is restricted to Vertex AI endpoints only
    --rebuild           Force rebuild of workspace container image
                        Use when devcontainer.json has changed
    --dry-run           Show configuration and what would be done, then exit
                        Useful for verifying paude.json or devcontainer.json
    --backend           Container backend to use: podman (default), openshift
    --openshift-context Kubeconfig context for OpenShift
    --openshift-namespace
                        OpenShift namespace (default: current context)
    --openshift-registry
                        Container registry URL (e.g., quay.io/myuser)
    --no-openshift-tls-verify
                        Disable TLS certificate verification when pushing images

CLAUDE OPTIONS:
    All arguments after -- are passed directly to claude.
    Run 'paude -- --help' to see claude's options.

EXAMPLES:
    paude                           Start interactive claude session
    paude --yolo                    Start with YOLO mode (no permission prompts)
    paude -- -p "What is 2+2?"      Run claude with a prompt
    paude --yolo -- -p "Fix bugs"   YOLO mode with a prompt
    paude -- --help                 Show claude's help
    paude --backend=openshift       Run on OpenShift cluster
    paude sessions --backend=openshift
                                    List OpenShift sessions
    paude sync --direction=both     Sync files with running session

SECURITY:
    By default, paude runs with network restricted to Google/Anthropic APIs only.
    Use --allow-network to permit all network access (enables data exfiltration).
    Combining --yolo with --allow-network is maximum risk mode."""
    typer.echo(help_text)


def help_callback(value: bool) -> None:
    """Print help and exit."""
    if value:
        show_help()
        raise typer.Exit()


@app.command()
def sessions(
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
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
) -> None:
    """List active sessions."""
    if backend == BackendType.podman:
        typer.echo("Podman sessions are ephemeral - no persistent sessions to list.")
        typer.echo("Use --backend=openshift to list OpenShift sessions.")
    else:
        from paude.backends import OpenShiftBackend, OpenShiftConfig
        from paude.backends.openshift import OpenShiftError

        try:
            os_config = OpenShiftConfig(
                context=openshift_context,
                namespace=openshift_namespace,
            )
            os_backend = OpenShiftBackend(config=os_config)
            session_list = os_backend.list_sessions()

            if not session_list:
                typer.echo("No active sessions.")
            else:
                typer.echo(f"{'ID':<20} {'STATUS':<12} {'CREATED':<25}")
                typer.echo("-" * 60)
                for s in session_list:
                    typer.echo(f"{s.id:<20} {s.status:<12} {s.created_at:<25}")
        except OpenShiftError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None


@app.command()
def attach(
    session_id: Annotated[
        str | None,
        typer.Argument(help="Session ID to attach to (most recent if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
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
) -> None:
    """Attach to an existing session."""
    if backend == BackendType.podman:
        typer.echo(
            "Podman sessions are ephemeral and cannot be reattached. "
            "Run 'paude' to start a new session."
        )
        raise typer.Exit(1)

    from paude.backends import OpenShiftBackend, OpenShiftConfig
    from paude.backends.openshift import OpenShiftError

    try:
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=os_config)

        # If no session ID provided, use the most recent
        if not session_id:
            session_list = os_backend.list_sessions()
            running = [s for s in session_list if s.status == "running"]
            if not running:
                typer.echo("No running sessions to attach to.", err=True)
                raise typer.Exit(1)
            session_id = running[0].id

        exit_code = os_backend.attach_session(session_id)
        raise typer.Exit(exit_code)
    except OpenShiftError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@app.command()
def stop(
    session_id: Annotated[
        str | None,
        typer.Argument(help="Session ID to stop (most recent if not specified)"),
    ] = None,
    all_sessions: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Stop all sessions.",
        ),
    ] = False,
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
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
) -> None:
    """Stop a session and clean up resources."""
    if backend == BackendType.podman:
        typer.echo("Podman sessions are ephemeral - no cleanup needed.")
        return

    from paude.backends import OpenShiftBackend, OpenShiftConfig
    from paude.backends.openshift import OpenShiftError

    try:
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=os_config)

        if all_sessions:
            session_list = os_backend.list_sessions()
            for s in session_list:
                os_backend.stop_session(s.id)
            typer.echo(f"Stopped {len(session_list)} session(s).")
        elif session_id:
            os_backend.stop_session(session_id)
        else:
            # Stop most recent session
            session_list = os_backend.list_sessions()
            if not session_list:
                typer.echo("No sessions to stop.", err=True)
                raise typer.Exit(1)
            os_backend.stop_session(session_list[0].id)
    except OpenShiftError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@app.command()
def sync(
    session_id: Annotated[
        str | None,
        typer.Argument(help="Session ID to sync (most recent if not specified)"),
    ] = None,
    direction: Annotated[
        str,
        typer.Option(
            "--direction", "-d",
            help="Sync direction: 'local' (pull), 'remote' (push), 'both'.",
        ),
    ] = "both",
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
) -> None:
    """Sync files between local workspace and OpenShift session."""
    from pathlib import Path

    from paude.backends import OpenShiftBackend, OpenShiftConfig
    from paude.backends.openshift import OpenShiftError

    if direction not in ("local", "remote", "both"):
        typer.echo(
            f"Invalid direction: {direction}. Use 'local', 'remote', or 'both'.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=os_config)

        # If no session ID provided, use the most recent running session
        if not session_id:
            session_list = os_backend.list_sessions()
            running = [s for s in session_list if s.status == "running"]
            if not running:
                typer.echo("No running sessions to sync with.", err=True)
                raise typer.Exit(1)
            session_id = running[0].id

        os_backend.sync_workspace(
            session_id=session_id,
            direction=direction,
            local_path=Path.cwd(),
        )
    except OpenShiftError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Show paude version and exit.",
        ),
    ] = False,
    help_opt: Annotated[
        bool,
        typer.Option(
            "--help",
            "-h",
            callback=help_callback,
            is_eager=True,
            help="Show this help message and exit.",
        ),
    ] = False,
    yolo: Annotated[
        bool,
        typer.Option(
            "--yolo",
            help="Enable YOLO mode (skip all permission prompts).",
        ),
    ] = False,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Allow unrestricted network access.",
        ),
    ] = False,
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
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
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
    openshift_registry: Annotated[
        str | None,
        typer.Option(
            "--openshift-registry",
            help="Container registry URL (e.g., quay.io/myuser).",
        ),
    ] = None,
    openshift_tls_verify: Annotated[
        bool,
        typer.Option(
            "--openshift-tls-verify/--no-openshift-tls-verify",
            help="Verify TLS certificates when pushing images.",
        ),
    ] = True,
    claude_args: Annotated[
        list[str] | None,
        typer.Argument(help="Arguments to pass to claude (after --)"),
    ] = None,
) -> None:
    """Run Claude Code in an isolated Podman container."""
    # If a subcommand is invoked, don't run the default
    if ctx.invoked_subcommand is not None:
        return

    # Store flags for use by other modules
    ctx.ensure_object(dict)
    ctx.obj["yolo"] = yolo
    ctx.obj["allow_network"] = allow_network
    ctx.obj["rebuild"] = rebuild
    ctx.obj["dry_run"] = dry_run
    ctx.obj["backend"] = backend.value
    ctx.obj["openshift_context"] = openshift_context
    ctx.obj["openshift_namespace"] = openshift_namespace
    ctx.obj["openshift_registry"] = openshift_registry
    ctx.obj["openshift_tls_verify"] = openshift_tls_verify
    ctx.obj["claude_args"] = claude_args or []

    if dry_run:
        from paude.dry_run import show_dry_run

        show_dry_run(ctx.obj)
        raise typer.Exit()

    # Route to appropriate backend
    if backend == BackendType.openshift:
        _run_openshift_backend(ctx)
    else:
        _run_podman_backend(ctx)


def _run_openshift_backend(ctx: typer.Context) -> None:
    """Run using OpenShift backend."""
    from pathlib import Path

    from paude.backends import OpenShiftBackend, OpenShiftConfig
    from paude.backends.openshift import (
        NamespaceNotFoundError,
        OcNotInstalledError,
        OcNotLoggedInError,
        OpenShiftError,
        RegistryNotAccessibleError,
    )
    from paude.config import detect_config, parse_config
    from paude.environment import build_environment

    yolo = ctx.obj["yolo"]
    allow_network = ctx.obj["allow_network"]
    claude_args = ctx.obj["claude_args"]
    openshift_context = ctx.obj["openshift_context"]
    openshift_namespace = ctx.obj["openshift_namespace"]
    openshift_registry = ctx.obj["openshift_registry"]
    openshift_tls_verify = ctx.obj["openshift_tls_verify"]

    workspace = Path.cwd()

    # Detect and parse config
    config_file = detect_config(workspace)
    config = None
    if config_file:
        try:
            config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Error parsing config: {e}", err=True)
            raise typer.Exit(1) from None

    # Build environment
    env = build_environment()
    if config and config.container_env:
        env.update(config.container_env)

    # Create OpenShift backend configuration
    os_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
        registry=openshift_registry,
        tls_verify=openshift_tls_verify,
    )

    backend = OpenShiftBackend(config=os_config)

    rebuild = ctx.obj["rebuild"]

    # Determine image to use
    # For OpenShift, we build locally and push to internal registry
    from paude.container import ImageManager

    # Get script directory for dev mode
    script_dir: Path | None = None
    dev_path = Path(__file__).parent.parent.parent
    if (dev_path / "containers" / "paude" / "Dockerfile").exists():
        script_dir = dev_path

    image_manager = ImageManager(script_dir=script_dir)

    # Build the image locally first
    if config and (config.base_image or config.dockerfile or config.pip_install):
        local_image = image_manager.ensure_custom_image(
            config, force_rebuild=rebuild, workspace=workspace
        )
    else:
        local_image = image_manager.ensure_default_image()

    # Push to OpenShift registry
    image = backend.ensure_image(local_image, force_push=rebuild)

    # Run Claude via OpenShift backend
    try:
        session = backend.start_session(
            image=image,
            workspace=workspace,
            env=env,
            mounts=[],  # OpenShift uses file sync, not mounts
            args=claude_args,
            workdir="/workspace",
            network_restricted=not allow_network,
            yolo=yolo,
        )
        exit_code = 0 if session.status == "stopped" else 1
    except OcNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except OcNotLoggedInError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except NamespaceNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo(
            "Hint: Use --openshift-namespace to specify an existing namespace.",
            err=True,
        )
        raise typer.Exit(1) from None
    except RegistryNotAccessibleError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except OpenShiftError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error running Claude on OpenShift: {e}", err=True)
        raise typer.Exit(1) from None

    raise typer.Exit(exit_code)


def _run_podman_backend(ctx: typer.Context) -> None:
    """Run using Podman backend."""
    import atexit
    import signal
    import sys
    from pathlib import Path

    from paude.backends import PodmanBackend
    from paude.config import detect_config, parse_config
    from paude.container import ImageManager, NetworkManager
    from paude.environment import build_environment, build_proxy_environment
    from paude.mounts import build_mounts, build_venv_mounts, get_venv_paths
    from paude.platform import check_macos_volumes, get_podman_machine_dns
    from paude.utils import check_git_safety, check_requirements

    yolo = ctx.obj["yolo"]
    allow_network = ctx.obj["allow_network"]
    rebuild = ctx.obj["rebuild"]
    claude_args = ctx.obj["claude_args"]

    # Get script directory for dev mode
    script_dir: Path | None = None
    dev_path = Path(__file__).parent.parent.parent
    if (dev_path / "containers" / "paude" / "Dockerfile").exists():
        script_dir = dev_path

    workspace = Path.cwd()
    home = Path.home()

    # Check requirements
    try:
        check_requirements()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Detect and parse config
    config_file = detect_config(workspace)
    config = None
    if config_file:
        try:
            config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Error parsing config: {e}", err=True)
            raise typer.Exit(1) from None

    # Create managers and backend
    image_manager = ImageManager(script_dir=script_dir)
    network_manager = NetworkManager()
    backend = PodmanBackend()

    # Track resources for cleanup
    proxy_container: str | None = None
    network_name: str | None = None

    def cleanup() -> None:
        """Clean up resources on exit."""
        if proxy_container:
            backend.stop_container(proxy_container)

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(143))

    # Ensure images
    try:
        if config and (config.base_image or config.dockerfile or config.pip_install):
            image = image_manager.ensure_custom_image(
                config, force_rebuild=rebuild, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image()
    except Exception as e:
        typer.echo(f"Error ensuring image: {e}", err=True)
        raise typer.Exit(1) from None

    # Build mounts and environment
    mounts = build_mounts(workspace, home)

    # Add venv shadow mounts (must come after workspace mount)
    venv_mode = config.venv if config else "auto"
    venv_mounts = build_venv_mounts(workspace, venv_mode)
    mounts.extend(venv_mounts)

    env = build_environment()

    # Add container env from config
    if config and config.container_env:
        env.update(config.container_env)

    # Add PAUDE_VENV_PATHS when pip_install is enabled
    if config and config.pip_install:
        venv_paths = get_venv_paths(workspace, config.venv)
        if venv_paths:
            env["PAUDE_VENV_PATHS"] = ":".join(str(p) for p in venv_paths)

    # Check macOS volumes
    if not check_macos_volumes(workspace, image):
        raise typer.Exit(1)

    # Check git safety
    check_git_safety(workspace)

    # Setup proxy if not allow-network
    if not allow_network:
        try:
            # Create internal network (reused across invocations)
            network_name = "paude-internal"
            network_manager.create_internal_network(network_name)

            # Start proxy
            proxy_image = image_manager.ensure_proxy_image()
            dns = get_podman_machine_dns()
            proxy_container = backend.run_proxy(proxy_image, network_name, dns)

            # Add proxy env vars
            env.update(build_proxy_environment(proxy_container))
        except Exception as e:
            typer.echo(f"Error setting up proxy: {e}", err=True)
            cleanup()
            raise typer.Exit(1) from None

    # Run postCreateCommand if present and this is first run
    workspace_marker = workspace / ".paude-initialized"
    if config and config.post_create_command and not workspace_marker.exists():
        typer.echo(f"Running postCreateCommand: {config.post_create_command}")
        success = backend.run_post_create(
            image=image,
            mounts=mounts,
            env=env,
            command=config.post_create_command,
            workdir=str(workspace),
            network=network_name,
        )
        if not success:
            typer.echo("Warning: postCreateCommand failed", err=True)
        else:
            try:
                workspace_marker.touch()
            except OSError:
                pass

    # Run Claude via backend
    try:
        session = backend.start_session(
            image=image,
            workspace=workspace,
            env=env,
            mounts=mounts,
            args=claude_args,
            workdir=str(workspace),
            network_restricted=not allow_network,
            yolo=yolo,
            network=network_name,
        )
        exit_code = 0 if session.status == "stopped" else 1
    except Exception as e:
        typer.echo(f"Error running Claude: {e}", err=True)
        cleanup()
        raise typer.Exit(1) from None

    cleanup()
    raise typer.Exit(exit_code)
