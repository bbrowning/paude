"""Typer CLI for paude."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from paude import __version__

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated Podman container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)

# Session subcommand group
session_app = typer.Typer(
    name="session",
    help="Manage persistent paude sessions.",
    add_completion=False,
)
app.add_typer(session_app, name="session")

# Known subcommand names - must be updated when adding new subcommands
SUBCOMMANDS = {"session", "sessions", "attach", "stop", "sync"}


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
    paude session <COMMAND> [OPTIONS]

SESSION COMMANDS:
    session create [NAME]   Create a new persistent session
    session delete NAME     Delete a session and all its resources
    session start [NAME]    Start a session and connect to it
    session stop [NAME]     Stop a session (preserves data)
    session connect [NAME]  Attach to a running session
    session list            List all sessions
    session sync [NAME]     Sync files between local and remote workspace

LEGACY COMMANDS:
    sessions            List active sessions (OpenShift backend, legacy)
    attach              Attach to an existing session (legacy)
    stop                Stop a session and clean up resources (legacy)
    sync                Sync files between local and remote workspace (legacy)

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
    paude                           Start interactive claude session (ephemeral)
    paude --yolo                    Start with YOLO mode (no permission prompts)
    paude -- -p "What is 2+2?"      Run claude with a prompt
    paude session create            Create a persistent session
    paude session start             Start and connect to a session
    paude session list              List all sessions
    paude session delete my-project --confirm
                                    Delete a session permanently

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


# Session subcommands


@session_app.command("create")
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
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Allow unrestricted network access.",
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
) -> None:
    """Create a new persistent session (does not start it)."""
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
        dev_path = Path(__file__).parent.parent
        if (dev_path / "containers" / "paude" / "Dockerfile").exists():
            script_dir = dev_path

        image_manager = ImageManager(script_dir=script_dir)

        # Ensure image
        try:
            has_custom = (
                config
                and (config.base_image or config.dockerfile or config.pip_install)
            )
            if has_custom and config is not None:
                image = image_manager.ensure_custom_image(
                    config, force_rebuild=False, workspace=workspace
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

        # Create session config
        session_config = SessionConfig(
            name=name,
            workspace=workspace,
            image=image,
            env=env,
            mounts=mounts,
            args=[],
            workdir=str(workspace),
            network_restricted=not allow_network,
            yolo=yolo,
        )

        try:
            backend_instance = PodmanBackend()
            session = backend_instance.create_session(session_config)
            typer.echo(f"Session '{session.name}' created.")
            typer.echo(f"Run 'paude session start {session.name}' to start it.")
        except SessionExistsError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error creating session: {e}", err=True)
            raise typer.Exit(1) from None
    else:
        # OpenShift backend
        from paude.backends.openshift import (
            OpenShiftBackend,
            OpenShiftConfig,
        )
        from paude.backends.openshift import (
            SessionExistsError as OpenshiftSessionExistsError,
        )
        from paude.container import ImageManager

        # Get script directory for dev mode
        os_script_dir: Path | None = None
        os_dev_path = Path(__file__).parent.parent
        if (os_dev_path / "containers" / "paude" / "Dockerfile").exists():
            os_script_dir = os_dev_path

        image_manager = ImageManager(script_dir=os_script_dir)

        # Ensure image (build and push to registry)
        try:
            has_custom = (
                config
                and (config.base_image or config.dockerfile or config.pip_install)
            )
            if has_custom and config is not None:
                image = image_manager.ensure_custom_image(
                    config, force_rebuild=False, workspace=workspace
                )
            else:
                image = image_manager.ensure_default_image()
        except Exception as e:
            typer.echo(f"Error ensuring image: {e}", err=True)
            raise typer.Exit(1) from None

        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
            registry=openshift_registry,
            tls_verify=openshift_tls_verify,
        )

        try:
            os_backend = OpenShiftBackend(config=openshift_config)

            # Push image to OpenShift registry
            typer.echo("Pushing image to OpenShift registry...")
            remote_image = os_backend.push_image(image)

            # Create session config
            session_config = SessionConfig(
                name=name,
                workspace=workspace,
                image=remote_image,
                env=env,
                mounts=[],  # OpenShift uses oc rsync, not mounts
                args=[],
                workdir=str(workspace),
                network_restricted=not allow_network,
                yolo=yolo,
                pvc_size=pvc_size,
                storage_class=storage_class,
            )

            session = os_backend.create_session(session_config)
            typer.echo(f"Session '{session.name}' created.")
            typer.echo(
                f"Run 'paude session start {session.name} --backend=openshift' "
                "to start it."
            )
        except OpenshiftSessionExistsError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error creating session: {e}", err=True)
            raise typer.Exit(1) from None


@session_app.command("delete")
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
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error deleting session: {e}", err=True)
            raise typer.Exit(1) from None


@session_app.command("start")
def session_start(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    no_sync: Annotated[
        bool,
        typer.Option(
            "--no-sync",
            help="Skip file synchronization before connecting.",
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
    """Start a session and connect to it."""
    from paude.backends import PodmanBackend, SessionNotFoundError

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                exit_code = backend_obj.start_session(name, sync=not no_sync)  # type: ignore[attr-defined]
                raise typer.Exit(exit_code)
            except Exception as e:
                typer.echo(f"Error starting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # Default to podman if no backend specified and no name
    if backend is None:
        backend = BackendType.podman

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
                    typer.echo(
                        "No sessions found. Create one with 'paude session create'.",
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
            exit_code = backend_instance.start_session(name, sync=not no_sync)
            raise typer.Exit(exit_code)
        except SessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error starting session: {e}", err=True)
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
                    typer.echo(
                        "No sessions found. Create one with "
                        "'paude session create --backend=openshift'.",
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
            exit_code = os_backend.start_session(name, sync=not no_sync)
            raise typer.Exit(exit_code)
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error starting session: {e}", err=True)
            raise typer.Exit(1) from None


@session_app.command("stop")
def session_stop(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    do_sync: Annotated[
        bool,
        typer.Option(
            "--sync",
            help="Sync files back to local before stopping.",
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
    """Stop a session (preserves data)."""
    from paude.backends import PodmanBackend

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                backend_obj.stop_session(name, sync=do_sync)  # type: ignore[attr-defined]
                typer.echo(f"Session '{name}' stopped.")
                return
            except Exception as e:
                typer.echo(f"Error stopping session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # Default to podman if no backend specified and no name
    if backend is None:
        backend = BackendType.podman

    if backend == BackendType.podman:
        backend_instance = PodmanBackend()

        # If no name provided, find running session for current workspace
        if not name:
            session = backend_instance.find_session_for_workspace(Path.cwd())
            if session:
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
            backend_instance.stop_session(name, sync=do_sync)
            typer.echo(f"Session '{name}' stopped.")
        except Exception as e:
            typer.echo(f"Error stopping session: {e}", err=True)
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

        os_backend = OpenShiftBackend(config=openshift_config)

        # If no name provided, find running session for current workspace
        if not name:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session:
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
            os_backend.stop_session(name, sync=do_sync)
            typer.echo(f"Session '{name}' stopped.")
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error stopping session: {e}", err=True)
            raise typer.Exit(1) from None


@session_app.command("connect")
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
    from paude.backends import PodmanBackend

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

    # Default to podman if no backend specified and no name
    if backend is None:
        backend = BackendType.podman

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


@session_app.command("list")
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


@session_app.command("sync")
def session_sync(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-select if not specified)"),
    ] = None,
    direction: Annotated[
        str,
        typer.Option(
            "--direction", "-d",
            help="Sync direction: 'local' (pull), 'remote' (push), 'both'.",
        ),
    ] = "both",
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
    """Sync files between local workspace and remote session."""
    from paude.backends import PodmanBackend

    if direction not in ("local", "remote", "both"):
        typer.echo(
            f"Invalid direction: {direction}. Use 'local', 'remote', or 'both'.",
            err=True,
        )
        raise typer.Exit(1)

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                backend_obj.sync_session(name, direction=direction)  # type: ignore[attr-defined]
                typer.echo(f"Synced session '{name}'.")
                return
            except Exception as e:
                typer.echo(f"Error syncing session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # Default to podman if no backend specified and no name
    if backend is None:
        backend = BackendType.podman

    if backend == BackendType.podman:
        backend_instance = PodmanBackend()

        # If no name provided, find session for current workspace
        if not name:
            session = backend_instance.find_session_for_workspace(Path.cwd())
            if session:
                name = session.name
            else:
                typer.echo(
                    "No session found for current workspace. Specify a name.",
                    err=True,
                )
                raise typer.Exit(1)

        try:
            backend_instance.sync_session(name, direction=direction)
            typer.echo(f"Synced session '{name}'.")
        except Exception as e:
            typer.echo(f"Error syncing session: {e}", err=True)
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

        os_backend = OpenShiftBackend(config=openshift_config)

        # If no name provided, find session for current workspace
        if not name:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session:
                name = session.name
            else:
                typer.echo(
                    "No session found for current workspace. Specify a name.",
                    err=True,
                )
                raise typer.Exit(1)

        try:
            os_backend.sync_session(name, direction=direction)
            typer.echo(f"Synced session '{name}'.")
        except OpenshiftSessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error syncing session: {e}", err=True)
            raise typer.Exit(1) from None


# Legacy commands (kept for backward compatibility)


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
    """List active sessions (legacy command - use 'session list' instead)."""
    if backend == BackendType.podman:
        from paude.backends import PodmanBackend

        backend_instance = PodmanBackend()
        session_list_all = backend_instance.list_sessions()

        if not session_list_all:
            typer.echo("No sessions found.")
            typer.echo("Use 'paude session create' to create a persistent session.")
        else:
            typer.echo(f"{'NAME':<25} {'STATUS':<12} {'WORKSPACE':<40}")
            typer.echo("-" * 80)
            for s in session_list_all:
                workspace_str = str(s.workspace)
                if len(workspace_str) > 40:
                    workspace_str = "..." + workspace_str[-37:]
                typer.echo(f"{s.name:<25} {s.status:<12} {workspace_str:<40}")
    else:
        from paude.backends import OpenShiftBackend, OpenShiftConfig
        from paude.backends.openshift import OpenShiftError

        try:
            os_config = OpenShiftConfig(
                context=openshift_context,
                namespace=openshift_namespace,
            )
            os_backend = OpenShiftBackend(config=os_config)
            session_list_openshift = os_backend.list_sessions()

            if not session_list_openshift:
                typer.echo("No active sessions.")
            else:
                typer.echo(f"{'ID':<20} {'STATUS':<12} {'CREATED':<25}")
                typer.echo("-" * 60)
                for s in session_list_openshift:
                    session_id = getattr(s, "name", getattr(s, "id", "unknown"))
                    typer.echo(f"{session_id:<20} {s.status:<12} {s.created_at:<25}")
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
    """Attach to session (legacy command - use 'session connect' instead)."""
    if backend == BackendType.podman:
        from paude.backends import PodmanBackend

        backend_instance = PodmanBackend()

        if session_id:
            exit_code = backend_instance.connect_session(session_id)
        else:
            # Find running sessions
            sessions = [
                s for s in backend_instance.list_sessions()
                if s.status == "running"
            ]
            if not sessions:
                typer.echo("No running sessions to attach to.", err=True)
                typer.echo("Use 'paude session start' to start a session.", err=True)
                raise typer.Exit(1)
            exit_code = backend_instance.connect_session(sessions[0].name)

        raise typer.Exit(exit_code)

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
            session_list_os = os_backend.list_sessions()
            running = [s for s in session_list_os if s.status == "running"]
            if not running:
                typer.echo("No running sessions to attach to.", err=True)
                raise typer.Exit(1)
            session_id = getattr(
                running[0], "name", getattr(running[0], "id", "unknown")
            )

        exit_code = os_backend.attach_session_legacy(session_id)
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
    """Stop a session (legacy command - use 'session stop' instead)."""
    if backend == BackendType.podman:
        from paude.backends import PodmanBackend

        backend_instance = PodmanBackend()

        if all_sessions:
            sessions = backend_instance.list_sessions()
            for s in sessions:
                if s.status == "running":
                    backend_instance.stop_session(s.name)
            running_count = len([s for s in sessions if s.status == "running"])
            typer.echo(f"Stopped {running_count} session(s).")
        elif session_id:
            backend_instance.stop_session(session_id)
        else:
            # Stop most recent running session
            sessions = [
                s for s in backend_instance.list_sessions()
                if s.status == "running"
            ]
            if not sessions:
                typer.echo("No running sessions to stop.", err=True)
                raise typer.Exit(1)
            backend_instance.stop_session(sessions[0].name)
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
            session_list_os = os_backend.list_sessions()
            for s in session_list_os:
                sid = getattr(s, "name", getattr(s, "id", ""))
                os_backend.stop_session(sid)
            typer.echo(f"Stopped {len(session_list_os)} session(s).")
        elif session_id:
            os_backend.stop_session(session_id)
        else:
            # Stop most recent session
            session_list_os = os_backend.list_sessions()
            if not session_list_os:
                typer.echo("No sessions to stop.", err=True)
                raise typer.Exit(1)
            sid = getattr(
                session_list_os[0], "name", getattr(session_list_os[0], "id", "")
            )
            os_backend.stop_session(sid)
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
    """Sync files between local workspace and OpenShift session (legacy)."""
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
            session_list_os = os_backend.list_sessions()
            running = [s for s in session_list_os if s.status == "running"]
            if not running:
                typer.echo("No running sessions to sync with.", err=True)
                raise typer.Exit(1)
            session_id = getattr(running[0], "name", getattr(running[0], "id", ""))

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
    # Handle case where first positional arg is a subcommand name
    # This happens because Typer's Argument captures all positional args
    # before subcommand routing occurs
    if claude_args and claude_args[0] in SUBCOMMANDS:
        cmd_name = claude_args[0]
        remaining_args = claude_args[1:]

        # Get the click command and invoke it with proper context
        click_app = ctx.command
        if cmd_name in click_app.commands:
            import click

            cmd = click_app.commands[cmd_name]
            try:
                sub_ctx = cmd.make_context(cmd_name, list(remaining_args), parent=ctx)
                with sub_ctx:
                    cmd.invoke(sub_ctx)
                raise typer.Exit(0)
            except click.exceptions.Exit as e:
                raise typer.Exit(e.exit_code) from None

    # If a subcommand is invoked through normal routing, don't run default
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
    """Run using OpenShift backend with persistent sessions."""
    from paude.backends import OpenShiftBackend, OpenShiftConfig, SessionConfig
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

    # Add claude args to environment
    if claude_args:
        env["PAUDE_CLAUDE_ARGS"] = " ".join(claude_args)

    # Create OpenShift backend configuration
    os_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
        registry=openshift_registry,
        tls_verify=openshift_tls_verify,
    )

    try:
        backend_instance = OpenShiftBackend(config=os_config)
    except OcNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except OcNotLoggedInError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    rebuild = ctx.obj["rebuild"]

    # Determine image to use
    # For OpenShift, we build locally and push to internal registry
    from paude.container import ImageManager

    # Get script directory for dev mode
    script_dir: Path | None = None
    dev_path = Path(__file__).parent.parent
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
    try:
        image = backend_instance.ensure_image(local_image, force_push=rebuild)
    except RegistryNotAccessibleError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Use persistent session workflow:
    # 1. Check for existing session for this workspace
    # 2. If found and running → connect
    # 3. If found and stopped → start
    # 4. If not found → create and start
    try:
        existing_session = backend_instance.find_session_for_workspace(workspace)

        if existing_session:
            session_name = existing_session.name
            if existing_session.status == "running":
                typer.echo(f"Connecting to running session '{session_name}'...")
                exit_code = backend_instance.connect_session(session_name)
            else:
                typer.echo(f"Starting existing session '{session_name}'...")
                exit_code = backend_instance.start_session(session_name, sync=True)
        else:
            # Create a new session
            session_config = SessionConfig(
                name=None,  # Auto-generate name
                workspace=workspace,
                image=image,
                env=env,
                mounts=[],  # OpenShift uses oc rsync, not mounts
                args=claude_args or [],
                workdir=str(workspace),
                network_restricted=not allow_network,
                yolo=yolo,
            )

            session = backend_instance.create_session(session_config)
            typer.echo(f"Created session '{session.name}'")
            exit_code = backend_instance.start_session(session.name, sync=True)

    except NamespaceNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo(
            "Hint: Use --openshift-namespace to specify an existing namespace.",
            err=True,
        )
        raise typer.Exit(1) from None
    except OpenShiftError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error running Claude on OpenShift: {e}", err=True)
        raise typer.Exit(1) from None

    raise typer.Exit(exit_code)


def _run_podman_backend(ctx: typer.Context) -> None:
    """Run using Podman backend (legacy ephemeral mode)."""
    import atexit
    import signal
    import sys

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
    dev_path = Path(__file__).parent.parent
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
    backend_instance = PodmanBackend()

    # Track resources for cleanup
    proxy_container: str | None = None
    network_name: str | None = None

    def cleanup() -> None:
        """Clean up resources on exit."""
        if proxy_container:
            backend_instance.stop_container(proxy_container)

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
            proxy_container = backend_instance.run_proxy(proxy_image, network_name, dns)

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
        success = backend_instance.run_post_create(
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

    # Run Claude via backend (legacy ephemeral mode)
    try:
        session = backend_instance.start_session_legacy(
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
