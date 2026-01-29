"""Typer CLI for paude."""

from __future__ import annotations

import os
import subprocess
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer

from paude import __version__

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)

class BackendType(str, Enum):
    """Container backend types."""

    podman = "podman"
    openshift = "openshift"


def find_session_backend(
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> tuple[BackendType, object] | None:
    """Find which backend contains the given session.

    Args:
        session_name: Name of the session to find.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Tuple of (backend_type, backend_instance) if found, None otherwise.
        The backend_instance is either PodmanBackend or OpenShiftBackend.
    """
    from paude.backends import PodmanBackend

    # Try Podman first
    try:
        podman = PodmanBackend()
        for session in podman.list_sessions():
            if session.name == session_name:
                return (BackendType.podman, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Try OpenShift
    try:
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=os_config)
        for session in os_backend.list_sessions():
            if session.name == session_name:
                return (BackendType.openshift, os_backend)
    except Exception:  # noqa: S110 - OpenShift may not be available
        pass

    return None


def version_callback(value: bool) -> None:
    """Print version information and exit."""
    if value:
        typer.echo(f"paude {__version__}")
        dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
        if dev_mode:
            typer.echo("  mode: development (PAUDE_DEV=1, building locally)")
        else:
            typer.echo(f"  mode: installed (pulling from {registry})")
        raise typer.Exit()


def show_help() -> None:
    """Show custom help message matching bash format."""
    help_text = """paude - Run Claude Code in a secure container

USAGE:
    paude                           List all sessions
    paude <COMMAND> [OPTIONS]

COMMANDS:
    create [NAME]       Create a new persistent session
    start [NAME]        Start a session and connect to it
    stop [NAME]         Stop a session (preserves data)
    connect [NAME]      Attach to a running session
    list                List all sessions
    remote <ACTION>     Manage git remotes for code sync
    delete NAME         Delete a session and all its resources

OPTIONS (for 'create' command):
    --yolo              Enable YOLO mode (skip all permission prompts)
    --allowed-domains   Domains to allow network access (repeatable).
                        Special: 'all' (unrestricted), 'default' (vertexai+pypi)
                        Aliases: 'vertexai', 'pypi'
                        Custom domains REPLACE defaults; use with 'default' to add.
    --rebuild           Force rebuild of workspace container image
    --dry-run           Show configuration without creating session
    -a, --args          Arguments to pass to claude (e.g., -a '-p "prompt"')
    --backend           Container backend: podman (default), openshift
    --platform          Target platform for image builds (e.g., linux/amd64)
    --openshift-context Kubeconfig context for OpenShift
    --openshift-namespace
                        OpenShift namespace (default: current context)

OPTIONS (global):
    -h, --help          Show this help message and exit
    -V, --version       Show paude version and exit

WORKFLOW:
    # Terminal 1:
    paude create my-project         # Create session
    paude start my-project          # Start and connect (stays attached)

    # Terminal 2 (while container running):
    paude remote add --push my-project  # Init git repo in container + push code

    # In container (Terminal 1):
    pip install -e .                # Install deps manually if needed

    # Later:
    paude connect                   # Reconnect to running session
    git push paude-<name> main      # Push more changes to container
    paude stop                      # Stop session (preserves data)
    paude delete NAME --confirm     # Delete session permanently

SYNCING CODE (via git):
    paude remote add [NAME]         Add git remote (requires running container)
    paude remote add --push [NAME]  Add remote AND push current branch
    paude remote list               List all paude git remotes
    paude remote remove [NAME]      Remove git remote for session
    git push paude-<name> main      Push code to container
    git pull paude-<name> main      Pull changes from container

EXAMPLES:
    paude create --yolo --allowed-domains all
                                    Create session with full autonomy (DANGEROUS)
    paude create --allowed-domains default --allowed-domains .example.com
                                    Add custom domain to defaults
    paude create --allowed-domains .example.com
                                    Allow ONLY custom domain (replaces defaults)
    paude create -a '-p "prompt"'   Create session with initial prompt
    paude create --dry-run          Verify configuration without creating
    paude create --backend=openshift
                                    Create session on OpenShift cluster

SECURITY:
    By default, paude runs with network restricted to Vertex AI and PyPI only.
    Use --allowed-domains all to permit all network access (enables data exfil).
    Combining --yolo with --allowed-domains all is maximum risk mode."""
    typer.echo(help_text)


def help_callback(value: bool) -> None:
    """Print help and exit."""
    if value:
        show_help()
        raise typer.Exit()


@app.command("create")
def session_create(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-generated if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
    yolo: Annotated[
        bool,
        typer.Option(
            "--yolo",
            help="Enable YOLO mode (skip all permission prompts).",
        ),
    ] = False,
    allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domains",
            help=(
                "Domains to allow network access. Can be repeated. "
                "Special values: 'all' (unrestricted), 'default' (vertexai+pypi), "
                "'vertexai', 'pypi'. Default: 'default'."
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
        str,
        typer.Option(
            "--pvc-size",
            help="PVC size for OpenShift (e.g., 10Gi).",
        ),
    ] = "10Gi",
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
) -> None:
    """Create a new persistent session (does not start it)."""
    import shlex

    from paude.dry_run import show_dry_run

    # Parse claude_args string into a list
    parsed_args: list[str] = []
    if claude_args:
        try:
            parsed_args = shlex.split(claude_args)
        except ValueError as e:
            typer.echo(f"Error parsing --args: {e}", err=True)
            raise typer.Exit(1) from None

    # Handle dry-run mode
    if dry_run:
        from paude.domains import expand_domains

        # Default to ["default"] if not specified
        domains_input = allowed_domains if allowed_domains else ["default"]
        expanded = expand_domains(domains_input)
        flags = {
            "yolo": yolo,
            "allowed_domains": expanded,
            "rebuild": rebuild,
            "backend": backend.value,
            "openshift_context": openshift_context,
            "openshift_namespace": openshift_namespace,
            "verbose": verbose,
            "claude_args": parsed_args,
        }
        show_dry_run(flags)
        raise typer.Exit()
    from paude.backends import (
        PodmanBackend,
        SessionConfig,
        SessionExistsError,
    )
    from paude.config import detect_config, parse_config
    from paude.container import ImageManager
    from paude.environment import build_environment
    from paude.mounts import build_mounts, build_venv_mounts

    workspace = Path.cwd()
    home = Path.home()

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

    if backend == BackendType.podman:
        # Get script directory for dev mode
        script_dir: Path | None = None
        dev_path = Path(__file__).parent.parent.parent
        if (dev_path / "containers" / "paude" / "Dockerfile").exists():
            script_dir = dev_path

        image_manager = ImageManager(script_dir=script_dir, platform=platform)

        # Ensure image
        try:
            has_custom = (
                config
                and (config.base_image or config.dockerfile)
            )
            if has_custom and config is not None:
                image = image_manager.ensure_custom_image(
                    config, force_rebuild=rebuild, workspace=workspace
                )
            else:
                image = image_manager.ensure_default_image()
        except Exception as e:
            typer.echo(f"Error ensuring image: {e}", err=True)
            raise typer.Exit(1) from None

        # Build mounts
        mounts = build_mounts(workspace, home)
        venv_mode = config.venv if config else "auto"
        venv_mounts = build_venv_mounts(workspace, venv_mode)
        mounts.extend(venv_mounts)

        # Expand allowed domains (default to ["default"] if not specified)
        from paude.domains import (
            expand_domains,
            format_domains_for_display,
            is_unrestricted,
        )

        domains_input = allowed_domains if allowed_domains else ["default"]
        expanded_domains = expand_domains(domains_input)

        # Show warnings for dangerous configurations
        if yolo and is_unrestricted(expanded_domains):
            typer.echo(
                "WARNING: Creating session with --yolo and unrestricted network.",
                err=True,
            )
            typer.echo(
                "         Claude can exfiltrate files without confirmation.",
                err=True,
            )
            typer.echo("", err=True)

        # Create session config
        session_config = SessionConfig(
            name=name,
            workspace=workspace,
            image=image,
            env=env,
            mounts=mounts,
            args=parsed_args,
            workdir=str(workspace),
            allowed_domains=expanded_domains,
            yolo=yolo,
        )

        try:
            backend_instance = PodmanBackend()
            session = backend_instance.create_session(session_config)
            typer.echo(f"Session '{session.name}' created.")
            domains_display = format_domains_for_display(expanded_domains)
            typer.echo(f"  Network: {domains_display}")
            if yolo:
                typer.echo("  Mode: YOLO (no permission prompts)")
            typer.echo("")
            typer.echo("To start working:")
            typer.echo("  paude start")
        except SessionExistsError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error creating session: {e}", err=True)
            raise typer.Exit(1) from None
    else:
        # OpenShift backend
        from paude.backends.openshift import (
            BuildFailedError,
            OpenShiftBackend,
            OpenShiftConfig,
            _generate_session_name,
        )
        from paude.backends.openshift import (
            SessionExistsError as OpenshiftSessionExistsError,
        )

        # Get script directory for dev mode
        os_script_dir: Path | None = None
        os_dev_path = Path(__file__).parent.parent.parent
        if (os_dev_path / "containers" / "paude" / "Dockerfile").exists():
            os_script_dir = os_dev_path

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        try:
            os_backend = OpenShiftBackend(config=openshift_config)

            # Pre-compute session name for labeling builds
            session_name = name if name else _generate_session_name(workspace)

            # Build image via OpenShift binary build
            typer.echo("Building image in OpenShift cluster...")
            image = os_backend.ensure_image_via_build(
                config=config,
                workspace=workspace,
                script_dir=os_script_dir,
                force_rebuild=rebuild,
                session_name=session_name,
            )

            # Expand allowed domains (default to ["default"] if not specified)
            from paude.domains import (
                expand_domains,
                format_domains_for_display,
                is_unrestricted,
            )

            domains_input = allowed_domains if allowed_domains else ["default"]
            expanded_domains = expand_domains(domains_input)

            # Build proxy image when needed (PAUDE_DEV=1 and proxy is used)
            proxy_image: str | None = None
            if not is_unrestricted(expanded_domains):
                dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
                if dev_mode and os_script_dir:
                    typer.echo("Building proxy image in OpenShift cluster...")
                    proxy_image = os_backend.ensure_proxy_image_via_build(
                        script_dir=os_script_dir,
                        force_rebuild=rebuild,
                        session_name=session_name,
                    )

            # Show warnings for dangerous configurations
            if yolo and is_unrestricted(expanded_domains):
                typer.echo(
                    "WARNING: Creating session with --yolo and unrestricted network.",
                    err=True,
                )
                typer.echo(
                    "         Claude can exfiltrate files to the internet "
                    "without confirmation.",
                    err=True,
                )
                typer.echo("", err=True)

            # Create session config
            session_config = SessionConfig(
                name=session_name,
                workspace=workspace,
                image=image,
                env=env,
                mounts=[],  # OpenShift uses oc rsync, not mounts
                args=parsed_args,
                workdir=str(workspace),
                allowed_domains=expanded_domains,
                yolo=yolo,
                pvc_size=pvc_size,
                storage_class=storage_class,
                proxy_image=proxy_image,
            )

            session = os_backend.create_session(session_config)
            typer.echo(f"Session '{session.name}' created.")
            domains_display = format_domains_for_display(expanded_domains)
            typer.echo(f"  Network: {domains_display}")
            if yolo:
                typer.echo("  Mode: YOLO (no permission prompts)")
            typer.echo("")
            typer.echo("Session is running. Connect with:")
            typer.echo("  paude connect --backend=openshift")
        except BuildFailedError as e:
            typer.echo(f"Build failed: {e}", err=True)
            raise typer.Exit(1) from None
        except OpenshiftSessionExistsError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error creating session: {e}", err=True)
            raise typer.Exit(1) from None


@app.command("delete")
def session_delete(
    name: Annotated[
        str,
        typer.Argument(help="Session name to delete"),
    ],
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Confirm deletion (required).",
        ),
    ] = False,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
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
) -> None:
    """Delete a session and all its resources permanently."""
    from paude.backends import PodmanBackend, SessionNotFoundError

    if not confirm:
        typer.echo(
            f"Deleting session '{name}' will permanently remove all data.",
            err=True,
        )
        typer.echo("Use --confirm to proceed.", err=True)
        raise typer.Exit(1)

    # Auto-detect backend if not specified
    if backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                backend_obj.delete_session(name, confirm=True)  # type: ignore[attr-defined]
                typer.echo(f"Session '{name}' deleted.")
                _cleanup_session_git_remote(name)
                return
            except Exception as e:
                typer.echo(f"Error deleting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    if backend == BackendType.podman:
        try:
            backend_instance = PodmanBackend()
            backend_instance.delete_session(name, confirm=True)
            typer.echo(f"Session '{name}' deleted.")
            _cleanup_session_git_remote(name)
        except SessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error deleting session: {e}", err=True)
            raise typer.Exit(1) from None
    else:
        from paude.backends.openshift import (
            OpenShiftBackend,
            OpenShiftConfig,
        )
        from paude.backends.openshift import (
            SessionNotFoundError as OpenshiftSessionNotFoundError,
        )

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        try:
            os_backend = OpenShiftBackend(config=openshift_config)
            os_backend.delete_session(name, confirm=True)
            typer.echo(f"Session '{name}' deleted.")
            _cleanup_session_git_remote(name)
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error deleting session: {e}", err=True)
            raise typer.Exit(1) from None


@app.command("start")
def session_start(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
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
) -> None:
    """Start a session and connect to it."""
    from paude.backends import PodmanBackend, SessionNotFoundError

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                exit_code = backend_obj.start_session(name)  # type: ignore[attr-defined]
                raise typer.Exit(exit_code)
            except Exception as e:
                typer.echo(f"Error starting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        from paude.backends import Session
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        workspace_session: Session | None = None
        workspace_backend: PodmanBackend | OpenShiftBackend | None = None
        podman_backend: PodmanBackend | None = None
        os_backend: OpenShiftBackend | None = None

        # Check podman for workspace session
        try:
            podman_backend = PodmanBackend()
            session = podman_backend.find_session_for_workspace(Path.cwd())
            if session:
                workspace_session = session
                workspace_backend = podman_backend
        except Exception:  # noqa: S110 - Podman may not be available
            pass

        # Check openshift for workspace session (only if not found in podman)
        if workspace_session is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                session = os_backend.find_session_for_workspace(Path.cwd())
                if session:
                    workspace_session = session
                    workspace_backend = os_backend
            except Exception:  # noqa: S110 - OpenShift may not be available
                pass

        # If workspace session found, start it
        if workspace_session and workspace_backend:
            typer.echo(
                f"Starting '{workspace_session.name}' "
                f"({workspace_session.backend_type})..."
            )
            exit_code = workspace_backend.start_session(workspace_session.name)
            raise typer.Exit(exit_code)

        # Collect all sessions from available backends
        all_sessions: list[tuple[Session, PodmanBackend | OpenShiftBackend]] = []

        if podman_backend:
            try:
                for s in podman_backend.list_sessions():
                    all_sessions.append((s, podman_backend))
            except Exception:  # noqa: S110
                pass

        if os_backend:
            try:
                for s in os_backend.list_sessions():
                    all_sessions.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        # Also try to initialize OpenShift if we haven't yet
        if os_backend is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                for s in os_backend.list_sessions():
                    all_sessions.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        if not all_sessions:
            typer.echo("No sessions found.", err=True)
            typer.echo("", err=True)
            typer.echo("To create and start a session:", err=True)
            typer.echo("  paude create && paude start", err=True)
            raise typer.Exit(1)
        if len(all_sessions) == 1:
            session, backend_obj = all_sessions[0]
            typer.echo(f"Starting '{session.name}' ({session.backend_type})...")
            exit_code = backend_obj.start_session(session.name)
            raise typer.Exit(exit_code)
        else:
            typer.echo(
                "Multiple sessions found. Specify one:",
                err=True,
            )
            typer.echo("", err=True)
            for s, _ in all_sessions:
                workspace_str = str(s.workspace)
                if len(workspace_str) > 35:
                    workspace_str = "..." + workspace_str[-32:]
                typer.echo(
                    f"  paude start {s.name}  # {s.backend_type}, {s.status}",
                    err=True,
                )
            raise typer.Exit(1)

    # Backend specified explicitly
    if backend == BackendType.podman:
        backend_instance = PodmanBackend()

        # If no name provided, find session for current workspace
        if not name:
            session = backend_instance.find_session_for_workspace(Path.cwd())
            if session:
                name = session.name
            else:
                # List all sessions and pick the first one
                sessions = backend_instance.list_sessions()
                if not sessions:
                    typer.echo("No sessions found for this workspace.", err=True)
                    typer.echo("", err=True)
                    typer.echo("To create and start a session:", err=True)
                    typer.echo("  paude create && paude start", err=True)
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo("Multiple sessions found. Specify a name:", err=True)
                    for s in sessions:
                        typer.echo(f"  {s.name} ({s.status})")
                    raise typer.Exit(1)

        try:
            exit_code = backend_instance.start_session(name)
            raise typer.Exit(exit_code)
        except SessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error starting session: {e}", err=True)
            raise typer.Exit(1) from None
    elif backend == BackendType.openshift:
        from paude.backends.openshift import (
            OpenShiftBackend,
            OpenShiftConfig,
        )
        from paude.backends.openshift import (
            SessionNotFoundError as OpenshiftSessionNotFoundError,
        )

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        os_backend = OpenShiftBackend(config=openshift_config)

        # If no name provided, find session for current workspace
        if not name:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session:
                name = session.name
            else:
                # List all sessions and pick the first one
                sessions = os_backend.list_sessions()
                if not sessions:
                    typer.echo("No sessions found for this workspace.", err=True)
                    typer.echo("", err=True)
                    typer.echo("To create and start a session:", err=True)
                    typer.echo(
                        "  paude create --backend=openshift && "
                        "paude start --backend=openshift",
                        err=True,
                    )
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo("Multiple sessions found. Specify a name:", err=True)
                    for s in sessions:
                        typer.echo(f"  {s.name} ({s.status})")
                    raise typer.Exit(1)

        try:
            exit_code = os_backend.start_session(name)
            raise typer.Exit(exit_code)
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error starting session: {e}", err=True)
            raise typer.Exit(1) from None


@app.command("stop")
def session_stop(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
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
) -> None:
    """Stop a session (preserves data)."""
    from paude.backends import PodmanBackend

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                backend_obj.stop_session(name)  # type: ignore[attr-defined]
                typer.echo(f"Session '{name}' stopped.")
                return
            except Exception as e:
                typer.echo(f"Error stopping session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        from paude.backends import Session
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        workspace_session: Session | None = None
        workspace_backend: PodmanBackend | OpenShiftBackend | None = None
        podman_backend: PodmanBackend | None = None
        os_backend: OpenShiftBackend | None = None

        # Check podman for workspace session
        try:
            podman_backend = PodmanBackend()
            session = podman_backend.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                workspace_session = session
                workspace_backend = podman_backend
        except Exception:  # noqa: S110 - Podman may not be available
            pass

        # Check openshift for workspace session (only if not found in podman)
        if workspace_session is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                session = os_backend.find_session_for_workspace(Path.cwd())
                if session and session.status == "running":
                    workspace_session = session
                    workspace_backend = os_backend
            except Exception:  # noqa: S110 - OpenShift may not be available
                pass

        # If workspace session found, stop it
        if workspace_session and workspace_backend:
            typer.echo(
                f"Stopping '{workspace_session.name}' "
                f"({workspace_session.backend_type})..."
            )
            workspace_backend.stop_session(workspace_session.name)
            typer.echo(f"Session '{workspace_session.name}' stopped.")
            return

        # Collect all running sessions from available backends
        all_sessions: list[tuple[Session, PodmanBackend | OpenShiftBackend]] = []

        if podman_backend:
            try:
                for s in podman_backend.list_sessions():
                    if s.status == "running":
                        all_sessions.append((s, podman_backend))
            except Exception:  # noqa: S110
                pass

        if os_backend:
            try:
                for s in os_backend.list_sessions():
                    if s.status == "running":
                        all_sessions.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        # Also try to initialize OpenShift if we haven't yet
        if os_backend is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                for s in os_backend.list_sessions():
                    if s.status == "running":
                        all_sessions.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        if not all_sessions:
            typer.echo("No running sessions to stop.", err=True)
            raise typer.Exit(1)
        if len(all_sessions) == 1:
            session, backend_obj = all_sessions[0]
            typer.echo(f"Stopping '{session.name}' ({session.backend_type})...")
            backend_obj.stop_session(session.name)
            typer.echo(f"Session '{session.name}' stopped.")
            return
        else:
            typer.echo(
                "Multiple running sessions found. Specify one:",
                err=True,
            )
            typer.echo("", err=True)
            for s, _ in all_sessions:
                typer.echo(
                    f"  paude stop {s.name}  # {s.backend_type}",
                    err=True,
                )
            raise typer.Exit(1)

    # Backend specified explicitly
    if backend == BackendType.podman:
        backend_instance = PodmanBackend()

        # If no name provided, find running session for current workspace
        if not name:
            session = backend_instance.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                name = session.name
            else:
                # Find running sessions
                sessions = [
                    s for s in backend_instance.list_sessions()
                    if s.status == "running"
                ]
                if not sessions:
                    typer.echo("No running sessions to stop.", err=True)
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo(
                        "Multiple running sessions found. Specify a name:",
                        err=True,
                    )
                    for s in sessions:
                        typer.echo(f"  {s.name}")
                    raise typer.Exit(1)

        try:
            backend_instance.stop_session(name)
            typer.echo(f"Session '{name}' stopped.")
        except Exception as e:
            typer.echo(f"Error stopping session: {e}", err=True)
            raise typer.Exit(1) from None
    elif backend == BackendType.openshift:
        from paude.backends.openshift import (
            OpenShiftBackend,
            OpenShiftConfig,
        )
        from paude.backends.openshift import (
            SessionNotFoundError as OpenshiftSessionNotFoundError,
        )

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        os_backend = OpenShiftBackend(config=openshift_config)

        # If no name provided, find running session for current workspace
        if not name:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                name = session.name
            else:
                # Find running sessions
                sessions = [
                    s for s in os_backend.list_sessions()
                    if s.status == "running"
                ]
                if not sessions:
                    typer.echo("No running sessions to stop.", err=True)
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo(
                        "Multiple running sessions found. Specify a name:",
                        err=True,
                    )
                    for s in sessions:
                        typer.echo(f"  {s.name}")
                    raise typer.Exit(1)

        try:
            os_backend.stop_session(name)
            typer.echo(f"Session '{name}' stopped.")
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error stopping session: {e}", err=True)
            raise typer.Exit(1) from None


@app.command("connect")
def session_connect(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
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
) -> None:
    """Attach to a running session."""
    from paude.backends import PodmanBackend, Session

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            exit_code = backend_obj.connect_session(name)  # type: ignore[attr-defined]
            raise typer.Exit(exit_code)
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        workspace_session: Session | None = None
        workspace_backend: PodmanBackend | OpenShiftBackend | None = None
        podman_backend: PodmanBackend | None = None
        os_backend: OpenShiftBackend | None = None

        # Check podman for workspace session
        try:
            podman_backend = PodmanBackend()
            session = podman_backend.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                workspace_session = session
                workspace_backend = podman_backend
        except Exception:  # noqa: S110 - Podman may not be available
            pass

        # Check openshift for workspace session (only if not found in podman)
        if workspace_session is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                session = os_backend.find_session_for_workspace(Path.cwd())
                if session and session.status == "running":
                    workspace_session = session
                    workspace_backend = os_backend
            except Exception:  # noqa: S110 - OpenShift may not be available
                pass

        # If workspace session found, connect to it
        if workspace_session and workspace_backend:
            typer.echo(
                f"Connecting to '{workspace_session.name}' "
                f"({workspace_session.backend_type})..."
            )
            exit_code = workspace_backend.connect_session(workspace_session.name)
            raise typer.Exit(exit_code)

        # Collect all running sessions from available backends
        all_running: list[tuple[Session, PodmanBackend | OpenShiftBackend]] = []

        if podman_backend:
            try:
                for s in podman_backend.list_sessions():
                    if s.status == "running":
                        all_running.append((s, podman_backend))
            except Exception:  # noqa: S110
                pass

        if os_backend:
            try:
                for s in os_backend.list_sessions():
                    if s.status == "running":
                        all_running.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        # Also try to initialize OpenShift if we haven't yet (workspace check skipped)
        if os_backend is None and workspace_session is None:
            try:
                os_config = OpenShiftConfig(
                    context=openshift_context,
                    namespace=openshift_namespace,
                )
                os_backend = OpenShiftBackend(config=os_config)
                for s in os_backend.list_sessions():
                    if s.status == "running":
                        all_running.append((s, os_backend))
            except Exception:  # noqa: S110
                pass

        if not all_running:
            typer.echo("No running sessions to connect to.", err=True)
            typer.echo("", err=True)
            typer.echo("To see all sessions:", err=True)
            typer.echo("  paude list", err=True)
            typer.echo("", err=True)
            typer.echo("To start a session:", err=True)
            typer.echo("  paude start", err=True)
            raise typer.Exit(1)
        if len(all_running) == 1:
            session, backend_obj = all_running[0]
            typer.echo(f"Connecting to '{session.name}' ({session.backend_type})...")
            exit_code = backend_obj.connect_session(session.name)
            raise typer.Exit(exit_code)
        else:
            typer.echo(
                "Multiple running sessions found. Specify one:",
                err=True,
            )
            typer.echo("", err=True)
            for s, _ in all_running:
                workspace_str = str(s.workspace)
                if len(workspace_str) > 35:
                    workspace_str = "..." + workspace_str[-32:]
                typer.echo(
                    f"  paude connect {s.name}  # {s.backend_type}, {workspace_str}",
                    err=True,
                )
            raise typer.Exit(1)

    # Backend specified explicitly
    if backend == BackendType.podman:
        backend_instance = PodmanBackend()

        # If no name provided, find running session for current workspace
        if not name:
            session = backend_instance.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                name = session.name
            else:
                # Find running sessions
                sessions = [
                    s for s in backend_instance.list_sessions()
                    if s.status == "running"
                ]
                if not sessions:
                    typer.echo("No running sessions to connect to.", err=True)
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo(
                        "Multiple running sessions found. Specify a name:",
                        err=True,
                    )
                    for s in sessions:
                        typer.echo(f"  {s.name}")
                    raise typer.Exit(1)

        exit_code = backend_instance.connect_session(name)
        raise typer.Exit(exit_code)
    else:
        from paude.backends.openshift import (
            OpenShiftBackend,
            OpenShiftConfig,
        )

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        os_backend = OpenShiftBackend(config=openshift_config)

        # If no name provided, find running session for current workspace
        if not name:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session and session.status == "running":
                name = session.name
            else:
                # Find running sessions
                sessions = [
                    s for s in os_backend.list_sessions()
                    if s.status == "running"
                ]
                if not sessions:
                    typer.echo("No running sessions to connect to.", err=True)
                    raise typer.Exit(1)
                if len(sessions) == 1:
                    name = sessions[0].name
                else:
                    typer.echo(
                        "Multiple running sessions found. Specify a name:",
                        err=True,
                    )
                    for s in sessions:
                        typer.echo(f"  {s.name}")
                    raise typer.Exit(1)

        exit_code = os_backend.connect_session(name)
        raise typer.Exit(exit_code)


@app.command("list")
def session_list(
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend to use (all backends if not specified).",
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
) -> None:
    """List all sessions."""
    from paude.backends import PodmanBackend

    all_sessions = []

    # Get Podman sessions
    if backend is None or backend == BackendType.podman:
        try:
            podman_backend = PodmanBackend()
            all_sessions.extend(podman_backend.list_sessions())
        except Exception:  # noqa: S110 - Podman may not be available
            pass

    # Get OpenShift sessions
    if backend is None or backend == BackendType.openshift:
        try:
            from paude.backends import OpenShiftBackend, OpenShiftConfig

            os_config = OpenShiftConfig(
                context=openshift_context,
                namespace=openshift_namespace,
            )
            os_backend = OpenShiftBackend(config=os_config)
            # OpenShift sessions use the old id field
            for session in os_backend.list_sessions():
                # Convert old Session format to new
                all_sessions.append(session)
        except Exception:  # noqa: S110 - OpenShift may not be available
            pass

    if not all_sessions:
        typer.echo("No sessions found.")
        typer.echo("")
        typer.echo("Quick start:")
        typer.echo("  paude create && paude start")
        typer.echo("")
        typer.echo("Or step by step:")
        typer.echo("  paude create       # Create session for this workspace")
        typer.echo("  paude start        # Start and connect to session")
        return

    # Print header
    typer.echo(f"{'NAME':<25} {'BACKEND':<12} {'STATUS':<12} {'WORKSPACE':<40}")
    typer.echo("-" * 90)

    for session in all_sessions:
        # Handle both old (id) and new (name) session formats
        session_name = getattr(session, "name", getattr(session, "id", "unknown"))
        workspace_str = str(session.workspace)
        if len(workspace_str) > 40:
            workspace_str = "..." + workspace_str[-37:]
        line = (
            f"{session_name:<25} {session.backend_type:<12} "
            f"{session.status:<12} {workspace_str:<40}"
        )
        typer.echo(line)


@app.command("remote")
def remote_command(
    action: Annotated[
        str,
        typer.Argument(help="Action: add, list, or remove"),
    ],
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (optional if only one exists)"),
    ] = None,
    push: Annotated[
        bool,
        typer.Option(
            "--push",
            help="Push current branch after adding remote (for 'add' action).",
        ),
    ] = False,
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
    """Manage git remotes for paude sessions.

    Actions:
      add [NAME]     Add a git remote for a session (uses ext:: protocol)
      list           List all paude git remotes
      remove [NAME]  Remove a git remote for a session
    """
    from paude.git_remote import (
        git_remote_remove,
        is_git_repository,
        list_paude_remotes,
    )

    if action == "list":
        remotes = list_paude_remotes()
        if not remotes:
            typer.echo("No paude git remotes found.")
            typer.echo("")
            typer.echo("To add a remote for a session:")
            typer.echo("  paude remote add [SESSION]")
            return

        typer.echo(f"{'REMOTE':<25} {'URL':<60}")
        typer.echo("-" * 85)
        for remote_name, remote_url in remotes:
            # Truncate URL if too long
            url_display = remote_url
            if len(url_display) > 60:
                url_display = url_display[:57] + "..."
            typer.echo(f"{remote_name:<25} {url_display:<60}")
        return

    if action == "add":
        if not is_git_repository():
            typer.echo("Error: Not a git repository.", err=True)
            typer.echo("Initialize git first: git init", err=True)
            raise typer.Exit(1)

        _remote_add(name, openshift_context, openshift_namespace, push=push)
        return

    if action == "remove":
        if not is_git_repository():
            typer.echo("Error: Not a git repository.", err=True)
            raise typer.Exit(1)

        if not name:
            # Auto-detect session for workspace
            sess, _ = _find_session_for_remote(openshift_context, openshift_namespace)
            if sess:
                name = sess.name
            else:
                typer.echo("Error: Specify a session name to remove.", err=True)
                raise typer.Exit(1)

        remote_name = f"paude-{name}"
        if git_remote_remove(remote_name):
            typer.echo(f"Removed git remote '{remote_name}'.")
        else:
            raise typer.Exit(1)
        return

    typer.echo(f"Unknown action: {action}", err=True)
    typer.echo("Valid actions: add, list, remove", err=True)
    raise typer.Exit(1)


def _cleanup_session_git_remote(session_name: str) -> None:
    """Remove git remote for a session if it exists in current directory.

    This is called after session deletion to clean up any associated git remote.
    Failures are silently ignored to not disrupt the deletion workflow.
    """
    from paude.git_remote import is_git_repository

    if not is_git_repository():
        return

    remote_name = f"paude-{session_name}"

    # Run git remote remove directly to handle "No such remote" silently
    result = subprocess.run(
        ["git", "remote", "remove", remote_name],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        typer.echo(f"Removed git remote '{remote_name}'.")
    elif "No such remote" not in result.stderr:
        # Warn about unexpected failures, but don't fail the delete
        err_msg = result.stderr.strip()
        typer.echo(f"Warning: Failed to remove git remote: {err_msg}", err=True)


def _find_session_for_remote(
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> tuple[Any, Any]:
    """Find a session for the current workspace.

    Returns:
        Tuple of (session, backend) if found, (None, None) otherwise.
    """
    from paude.backends import PodmanBackend

    # Try Podman first
    try:
        podman = PodmanBackend()
        session = podman.find_session_for_workspace(Path.cwd())
        if session:
            return (session, podman)
    except Exception:  # noqa: S110
        pass

    # Try OpenShift
    try:
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=os_config)
        session = os_backend.find_session_for_workspace(Path.cwd())
        if session:
            return (session, os_backend)
    except Exception:  # noqa: S110
        pass

    return (None, None)


def _remote_add(
    name: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
    push: bool = False,
) -> None:
    """Add a git remote for a session."""
    from paude.git_remote import (
        build_openshift_remote_url,
        build_podman_remote_url,
        enable_ext_protocol,
        get_current_branch,
        git_push_to_remote,
        git_remote_add,
        initialize_container_workspace_openshift,
        initialize_container_workspace_podman,
        is_container_running_podman,
        is_ext_protocol_allowed,
        is_pod_running_openshift,
    )

    # Check if ext protocol is enabled (required for ext:: remotes)
    if not is_ext_protocol_allowed():
        typer.echo("Enabling git ext:: protocol for this repository...", err=True)
        if not enable_ext_protocol():
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            typer.echo(
                "Run manually: git config protocol.ext.allow always",
                err=True,
            )
            raise typer.Exit(1)

    # Find the session
    session = None
    backend_obj = None

    if name:
        # Look up by name
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            _, backend_obj = result
            session = backend_obj.get_session(name)  # type: ignore[attr-defined]
    else:
        # Auto-detect from workspace
        session, backend_obj = _find_session_for_remote(
            openshift_context, openshift_namespace
        )

    if not session:
        typer.echo("Error: No session found.", err=True)
        if name:
            typer.echo(f"Session '{name}' does not exist.", err=True)
        else:
            typer.echo("No session exists for current workspace.", err=True)
            typer.echo("", err=True)
            typer.echo("Create one first:", err=True)
            typer.echo("  paude create", err=True)
        raise typer.Exit(1)

    # Build the remote URL based on backend type
    remote_name = f"paude-{session.name}"
    branch = get_current_branch() or "main"

    if session.backend_type == "openshift":
        from paude.backends.openshift import OpenShiftConfig

        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        # Get namespace
        if os_config.namespace:
            namespace = os_config.namespace
        else:
            try:
                from paude.backends.openshift import OpenShiftBackend

                os_backend = OpenShiftBackend(config=os_config)
                namespace = os_backend.namespace
            except Exception:
                namespace = "default"

        pod_name = f"paude-{session.name}-0"

        # Check if pod is running (live check, not cached status)
        if not is_pod_running_openshift(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
        ):
            typer.echo("Error: Container not running.", err=True)
            typer.echo("Start it first:", err=True)
            typer.echo(f"  paude start {session.name}", err=True)
            raise typer.Exit(1)

        # Initialize git repository in container
        typer.echo("Initializing git repository in container...")
        if not initialize_container_workspace_openshift(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
            branch=branch,
        ):
            raise typer.Exit(1)

        remote_url = build_openshift_remote_url(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
        )
    else:
        container_name = f"paude-{session.name}"

        # Check if container is running
        if not is_container_running_podman(container_name):
            typer.echo("Error: Container not running.", err=True)
            typer.echo("Start it first:", err=True)
            typer.echo(f"  paude start {session.name}", err=True)
            raise typer.Exit(1)

        # Initialize git repository in container
        typer.echo("Initializing git repository in container...")
        if not initialize_container_workspace_podman(container_name, branch=branch):
            raise typer.Exit(1)

        remote_url = build_podman_remote_url(container_name=container_name)

    # Add the remote
    if git_remote_add(remote_name, remote_url):
        typer.echo(f"Added git remote '{remote_name}'.")

        if push:
            typer.echo("")
            typer.echo(f"Pushing {branch} to container...")
            if not git_push_to_remote(remote_name, branch):
                typer.echo("Push failed.", err=True)
                raise typer.Exit(1)
            typer.echo("Push complete.")
        else:
            typer.echo("")
            typer.echo("Usage:")
            typer.echo(f"  git push {remote_name} {branch}  # Push code to container")
            typer.echo(f"  git pull {remote_name} {branch}  # Pull changes")
            typer.echo(f"  git fetch {remote_name}          # Fetch without merging")
    else:
        raise typer.Exit(1)


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
) -> None:
    """Run Claude Code in an isolated container."""
    # If a subcommand is invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    # Bare 'paude' command shows session list
    session_list()
