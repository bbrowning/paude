"""Typer CLI for paude."""

from __future__ import annotations

import os
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from paude import __version__
from paude.backends import (
    PodmanBackend,
    SessionConfig,
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.base import Backend, Session
from paude.backends.openshift import (
    BuildFailedError,
    OpenShiftBackend,
    OpenShiftConfig,
)
from paude.backends.openshift import (
    SessionExistsError as OpenshiftSessionExistsError,
)
from paude.backends.openshift import (
    SessionNotFoundError as OpenshiftSessionNotFoundError,
)
from paude.session_discovery import (
    collect_all_sessions,
    create_openshift_backend,
    find_workspace_session,
    resolve_session_for_backend,
)

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)


class BackendType(StrEnum):
    """Container backend types."""

    podman = "podman"
    openshift = "openshift"


def find_session_backend(
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> tuple[BackendType, Backend] | None:
    """Find which backend contains the given session.

    Args:
        session_name: Name of the session to find.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Tuple of (backend_type, backend_instance) if found, None otherwise.
        The backend_instance is either PodmanBackend or OpenShiftBackend.
    """
    # Try Podman first
    try:
        podman = PodmanBackend()
        for session in podman.list_sessions():
            if session.name == session_name:
                return (BackendType.podman, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Try OpenShift
    os_backend = create_openshift_backend(openshift_context, openshift_namespace)
    if os_backend is not None:
        try:
            for session in os_backend.list_sessions():
                if session.name == session_name:
                    return (BackendType.openshift, os_backend)
        except Exception:  # noqa: S110
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
    cp SRC DEST         Copy files between local and session
    remote <ACTION>     Manage git remotes for code sync
    allowed-domains NAME
                        Manage allowed egress domains for a session
    delete NAME         Delete a session and all its resources

OPTIONS (for 'create' command):
    --yolo              Enable YOLO mode (skip all permission prompts)
    --allowed-domains   Domains to allow network access (repeatable).
                        Special: 'all' (unrestricted), 'default' (vertexai+pypi+github)
                        Aliases: 'vertexai', 'pypi', 'github'
                        Custom domains REPLACE defaults; use with 'default' to add.
    --git               Set up git remote, push code+tags, configure origin
    --rebuild           Force rebuild of workspace container image
    --dry-run           Show configuration without creating session
    -a, --args          Arguments to pass to claude (e.g., -a '-p "prompt"')
    --backend           Container backend: podman (default), openshift
    --platform          Target platform for image builds (e.g., linux/amd64)
    --openshift-context Kubeconfig context for OpenShift
    --openshift-namespace
                        OpenShift namespace (default: current context)

OPTIONS (for 'start' and 'connect' commands):
    --github-token      GitHub personal access token for gh CLI.
                        Use a fine-grained read-only PAT.
                        Also reads PAUDE_GITHUB_TOKEN env var (flag takes priority).
                        Token is injected at connect time only, never stored.

OPTIONS (global):
    -h, --help          Show this help message and exit
    -V, --version       Show paude version and exit

WORKFLOW:
    # Quick start (create + push code in one step):
    paude create my-project --git   # Create, start, push code+tags, set origin
    paude connect my-project        # Connect to running session

    # Manual workflow:
    paude create my-project         # Create and start session
    paude remote add --push my-project  # Init git repo + push code
    paude connect my-project        # Connect to running session

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

COPYING FILES (without git):
    paude cp ./file.txt my-session:file.txt     Copy local file to session
    paude cp my-session:output.log ./           Copy file from session to local
    paude cp ./src :src                         Auto-detect session, copy dir
    paude cp :results ./results                 Auto-detect session, copy from

EGRESS FILTERING:
    paude allowed-domains my-session                      Show current domains
    paude allowed-domains my-session --add .example.com   Add domain to list
    paude allowed-domains my-session --remove .pypi.org   Remove domain
    paude allowed-domains my-session --replace default .example.com
                                                          Replace entire list

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
    By default, paude runs with network restricted to Vertex AI, PyPI, and GitHub.
    Use --allowed-domains all to permit all network access (enables data exfil).
    Combining --yolo with --allowed-domains all is maximum risk mode.
    PAUDE_GITHUB_TOKEN is explicit only; host GH_TOKEN is never auto-propagated."""
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
    credential_timeout: Annotated[
        int,
        typer.Option(
            "--credential-timeout",
            help="Inactivity minutes before removing credentials (OpenShift).",
        ),
    ] = 60,
    git: Annotated[
        bool,
        typer.Option(
            "--git",
            help="Set up git remote, push code+tags, configure origin.",
        ),
    ] = False,
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
            has_custom = config and (config.base_image or config.dockerfile)
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

        # Ensure proxy image when domain filtering is active
        podman_proxy_image: str | None = None
        if not is_unrestricted(expanded_domains):
            try:
                podman_proxy_image = image_manager.ensure_proxy_image()
            except Exception as e:
                typer.echo(f"Error ensuring proxy image: {e}", err=True)
                raise typer.Exit(1) from None

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
            proxy_image=podman_proxy_image,
        )

        try:
            backend_instance = PodmanBackend()
            session = backend_instance.create_session(session_config)

            # Auto-start the container (entrypoint is sleep infinity)
            backend_instance.start_session_no_attach(session.name)

            typer.echo(f"Session '{session.name}' created and running.")
            domains_display = format_domains_for_display(expanded_domains)
            typer.echo(f"  Network: {domains_display}")
            if yolo:
                typer.echo("  Mode: YOLO (no permission prompts)")

            if git:
                _setup_git_after_create(
                    session_name=session.name,
                    backend_type="podman",
                )

            typer.echo("")
            typer.echo("To start working:")
            typer.echo(f"  paude connect {session.name}")
        except SessionExistsError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error creating session: {e}", err=True)
            raise typer.Exit(1) from None
    else:
        # OpenShift backend
        from paude.backends.openshift import _generate_session_name

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
                credential_timeout=credential_timeout,
            )

            session = os_backend.create_session(session_config)
            typer.echo(f"Session '{session.name}' created.")
            domains_display = format_domains_for_display(expanded_domains)
            typer.echo(f"  Network: {domains_display}")
            if yolo:
                typer.echo("  Mode: YOLO (no permission prompts)")

            if git:
                _setup_git_after_create(
                    session_name=session.name,
                    backend_type="openshift",
                    openshift_context=openshift_context,
                    openshift_namespace=os_backend.namespace,
                )

            typer.echo("")
            typer.echo("Session is running. Connect with:")
            typer.echo(f"  paude connect {session.name}")
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
                backend_obj.delete_session(name, confirm=True)
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
    github_token: Annotated[
        str | None,
        typer.Option(
            "--github-token",
            help=(
                "GitHub personal access token for gh CLI. "
                "Use a fine-grained read-only PAT. "
                "Also reads PAUDE_GITHUB_TOKEN env var (this flag takes priority). "
                "Token is injected at connect time only, never stored."
            ),
        ),
    ] = None,
) -> None:
    """Start a session and connect to it."""
    # Resolve token: explicit flag takes priority over env var
    resolved_token = github_token or os.environ.get("PAUDE_GITHUB_TOKEN")

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                exit_code = backend_obj.start_session(name, github_token=resolved_token)
                raise typer.Exit(exit_code)
            except Exception as e:
                typer.echo(f"Error starting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        # No status filter: start includes all sessions (even stopped)
        workspace_match = find_workspace_session(openshift_context, openshift_namespace)
        if workspace_match:
            ws_session, ws_backend = workspace_match
            typer.echo(f"Starting '{ws_session.name}' ({ws_session.backend_type})...")
            exit_code = ws_backend.start_session(
                ws_session.name, github_token=resolved_token
            )
            raise typer.Exit(exit_code)

        all_sessions = collect_all_sessions(openshift_context, openshift_namespace)
        if not all_sessions:
            typer.echo("No sessions found.", err=True)
            typer.echo("", err=True)
            typer.echo("To create and start a session:", err=True)
            typer.echo("  paude create && paude start", err=True)
            raise typer.Exit(1)
        if len(all_sessions) == 1:
            session, backend_obj = all_sessions[0]
            typer.echo(f"Starting '{session.name}' ({session.backend_type})...")
            exit_code = backend_obj.start_session(
                session.name, github_token=resolved_token
            )
            raise typer.Exit(exit_code)
        else:
            typer.echo(
                "Multiple sessions found. Specify one:",
                err=True,
            )
            typer.echo("", err=True)
            for s, _ in all_sessions:
                typer.echo(
                    f"  paude start {s.name}  # {s.backend_type}, {s.status}",
                    err=True,
                )
            raise typer.Exit(1)

    # Backend specified explicitly
    if backend == BackendType.podman:
        backend_instance = PodmanBackend()
        if not name:
            name = resolve_session_for_backend(backend_instance)
            if not name:
                raise typer.Exit(1)

        try:
            exit_code = backend_instance.start_session(
                name, github_token=resolved_token
            )
            raise typer.Exit(exit_code)
        except SessionNotFoundError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1) from None
        except Exception as e:
            typer.echo(f"Error starting session: {e}", err=True)
            raise typer.Exit(1) from None
    elif backend == BackendType.openshift:
        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=openshift_config)
        if not name:
            name = resolve_session_for_backend(os_backend)
            if not name:
                raise typer.Exit(1)

        try:
            exit_code = os_backend.start_session(name, github_token=resolved_token)
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
    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            try:
                backend_obj.stop_session(name)
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
        # Only running sessions can be stopped
        workspace_match = find_workspace_session(
            openshift_context, openshift_namespace, status_filter="running"
        )
        if workspace_match:
            ws_session, ws_backend = workspace_match
            typer.echo(f"Stopping '{ws_session.name}' ({ws_session.backend_type})...")
            ws_backend.stop_session(ws_session.name)
            typer.echo(f"Session '{ws_session.name}' stopped.")
            return

        all_sessions = collect_all_sessions(
            openshift_context, openshift_namespace, status_filter="running"
        )
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
        if not name:
            name = resolve_session_for_backend(
                backend_instance, status_filter="running"
            )
            if not name:
                raise typer.Exit(1)

        try:
            backend_instance.stop_session(name)
            typer.echo(f"Session '{name}' stopped.")
        except Exception as e:
            typer.echo(f"Error stopping session: {e}", err=True)
            raise typer.Exit(1) from None
    elif backend == BackendType.openshift:
        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=openshift_config)
        if not name:
            name = resolve_session_for_backend(os_backend, status_filter="running")
            if not name:
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
    github_token: Annotated[
        str | None,
        typer.Option(
            "--github-token",
            help=(
                "GitHub personal access token for gh CLI. "
                "Use a fine-grained read-only PAT. "
                "Also reads PAUDE_GITHUB_TOKEN env var (this flag takes priority). "
                "Token is injected at connect time only, never stored."
            ),
        ),
    ] = None,
) -> None:
    """Attach to a running session."""
    # Resolve token: explicit flag takes priority over env var
    resolved_token = github_token or os.environ.get("PAUDE_GITHUB_TOKEN")

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            exit_code = backend_obj.connect_session(name, github_token=resolved_token)
            raise typer.Exit(exit_code)
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        # Only running sessions can be connected to
        workspace_match = find_workspace_session(
            openshift_context, openshift_namespace, status_filter="running"
        )
        if workspace_match:
            ws_session, ws_backend = workspace_match
            typer.echo(
                f"Connecting to '{ws_session.name}' ({ws_session.backend_type})..."
            )
            exit_code = ws_backend.connect_session(
                ws_session.name, github_token=resolved_token
            )
            raise typer.Exit(exit_code)

        all_running = collect_all_sessions(
            openshift_context, openshift_namespace, status_filter="running"
        )
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
            exit_code = backend_obj.connect_session(
                session.name, github_token=resolved_token
            )
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
        if not name:
            name = resolve_session_for_backend(
                backend_instance, status_filter="running"
            )
            if not name:
                raise typer.Exit(1)

        exit_code = backend_instance.connect_session(name, github_token=resolved_token)
        raise typer.Exit(exit_code)
    else:
        openshift_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        os_backend = OpenShiftBackend(config=openshift_config)
        if not name:
            name = resolve_session_for_backend(os_backend, status_filter="running")
            if not name:
                raise typer.Exit(1)

        exit_code = os_backend.connect_session(name, github_token=resolved_token)
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
        os_backend = create_openshift_backend(openshift_context, openshift_namespace)
        if os_backend is not None:
            try:
                all_sessions.extend(os_backend.list_sessions())
            except Exception:  # noqa: S110
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


def _parse_copy_path(path_arg: str) -> tuple[str | None, str]:
    """Parse a copy path argument into (session_name, path).

    Returns:
        Tuple of (session_name, path) where session_name is:
        - None for local paths
        - "" for auto-detect (`:path` syntax)
        - session name for explicit (`session:path` syntax)
    """
    # Paths starting with / or . are always local
    if path_arg.startswith("/") or path_arg.startswith("."):
        return (None, path_arg)

    # Contains colon -> remote path
    if ":" in path_arg:
        session_part, path_part = path_arg.split(":", 1)
        return (session_part, path_part)

    # No colon, no / or . prefix -> local path
    return (None, path_arg)


@app.command("cp")
def session_cp(
    src: Annotated[
        str,
        typer.Argument(help="Source path (local or session:path)"),
    ],
    dest: Annotated[
        str,
        typer.Argument(help="Destination path (local or session:path)"),
    ],
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
    """Copy files between local and a session."""
    src_session, src_path = _parse_copy_path(src)
    dest_session, dest_path = _parse_copy_path(dest)

    # Validate exactly one side is remote
    if src_session is None and dest_session is None:
        typer.echo(
            "Error: One of SRC or DEST must be a remote path (session:path).", err=True
        )
        typer.echo("", err=True)
        typer.echo("Examples:", err=True)
        typer.echo("  paude cp ./file.txt my-session:file.txt", err=True)
        typer.echo("  paude cp my-session:output.log ./", err=True)
        raise typer.Exit(1)

    if src_session is not None and dest_session is not None:
        typer.echo(
            "Error: Only one of SRC or DEST can be a remote path, not both.",
            err=True,
        )
        raise typer.Exit(1)

    # Determine direction and session name
    if dest_session is not None:
        # Local -> Remote
        session_name = dest_session
        remote_path = dest_path
        copy_direction = "to"
    else:
        # Remote -> Local (src_session is guaranteed non-None here)
        session_name = src_session  # type: ignore[assignment]
        remote_path = src_path
        copy_direction = "from"

    # Resolve session
    backend_obj: Backend | None = None
    if session_name:
        # Explicit session name
        result = find_session_backend(
            session_name, openshift_context, openshift_namespace
        )
        if result is None:
            typer.echo(f"Session '{session_name}' not found.", err=True)
            raise typer.Exit(1)
        _, backend_obj = result
    else:
        # Auto-detect session (empty string from `:path` syntax)
        workspace_match = find_workspace_session(
            openshift_context, openshift_namespace, status_filter="running"
        )
        if workspace_match:
            ws_session, backend_obj = workspace_match
            session_name = ws_session.name
        else:
            all_running = collect_all_sessions(
                openshift_context, openshift_namespace, status_filter="running"
            )
            if not all_running:
                typer.echo("No running sessions found.", err=True)
                raise typer.Exit(1)
            if len(all_running) == 1:
                session_obj, backend_obj = all_running[0]
                session_name = session_obj.name
            else:
                typer.echo(
                    "Multiple running sessions found. Specify one:",
                    err=True,
                )
                typer.echo("", err=True)
                for s, _ in all_running:
                    typer.echo(f"  paude cp ... {s.name}:path", err=True)
                raise typer.Exit(1)

    # Resolve relative remote paths to /pvc/workspace/
    if not remote_path.startswith("/"):
        remote_path = f"/pvc/workspace/{remote_path}"

    # Execute copy
    try:
        if copy_direction == "to":
            backend_obj.copy_to_session(session_name, src_path, remote_path)
            typer.echo(f"Copied '{src_path}' -> '{session_name}:{remote_path}'")
        else:
            backend_obj.copy_from_session(session_name, remote_path, dest_path)
            typer.echo(f"Copied '{session_name}:{remote_path}' -> '{dest_path}'")
    except (SessionNotFoundError, OpenshiftSessionNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error copying: {e}", err=True)
        raise typer.Exit(1) from None


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
) -> tuple[Session | None, Backend | None]:
    """Find a session for the current workspace.

    Returns:
        Tuple of (session, backend) if found, (None, None) otherwise.
    """
    # Try Podman first
    try:
        podman = PodmanBackend()
        session = podman.find_session_for_workspace(Path.cwd())
        if session:
            return (session, podman)
    except Exception:  # noqa: S110
        pass

    # Try OpenShift
    os_backend = create_openshift_backend(openshift_context, openshift_namespace)
    if os_backend is not None:
        try:
            session = os_backend.find_session_for_workspace(Path.cwd())
            if session:
                return (session, os_backend)
        except Exception:  # noqa: S110
            pass

    return (None, None)


def _setup_git_after_create(
    session_name: str,
    backend_type: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> bool:
    """Set up git remote, push code and tags, and configure origin after create.

    Args:
        session_name: Name of the created session.
        backend_type: "podman" or "openshift".
        openshift_context: OpenShift context (if applicable).
        openshift_namespace: OpenShift namespace (if applicable).

    Returns:
        True if all steps succeeded, False if any step failed.
    """
    from paude.git_remote import (
        fetch_tags_in_container_openshift,
        fetch_tags_in_container_podman,
        get_current_branch,
        get_local_origin_url,
        git_push_tags_to_remote,
        git_push_to_remote,
        is_git_repository,
        set_origin_in_container_openshift,
        set_origin_in_container_podman,
        ssh_url_to_https,
    )

    if not is_git_repository():
        typer.echo(
            "Warning: Not in a git repository. Skipping --git setup.",
            err=True,
        )
        return False

    typer.echo("")
    typer.echo("Setting up git...")

    # Step 1: Add remote and init git in container (without pushing)
    _remote_add(
        name=session_name,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        push=False,
    )

    # Step 2: Push current branch
    remote_name = f"paude-{session_name}"
    branch = get_current_branch() or "main"
    typer.echo(f"Pushing {branch} to container...")
    if not git_push_to_remote(remote_name, branch):
        typer.echo("Warning: Failed to push branch.", err=True)
        return False

    # Step 3: Push tags
    typer.echo("Pushing tags...")
    if not git_push_tags_to_remote(remote_name):
        typer.echo("Warning: Failed to push tags.", err=True)
        # Non-fatal, continue

    # Step 4: Set origin in container if local origin exists
    origin_url = get_local_origin_url()
    if origin_url:
        # Convert SSH URLs to HTTPS since the container has no SSH keys
        origin_url = ssh_url_to_https(origin_url)
        typer.echo(f"Setting origin in container to {origin_url}...")
        if backend_type == "podman":
            container_name = f"paude-{session_name}"
            origin_set = set_origin_in_container_podman(container_name, origin_url)
        else:
            pod_name = f"paude-{session_name}-0"
            namespace = openshift_namespace or "default"
            origin_set = set_origin_in_container_openshift(
                pod_name, namespace, origin_url, context=openshift_context,
            )

        # Step 5: Fetch tags from origin in container
        if origin_set:
            typer.echo("Fetching tags from origin in container...")
            if backend_type == "podman":
                if not fetch_tags_in_container_podman(container_name):
                    typer.echo(
                        "Warning: Could not fetch tags from origin "
                        "(network may be restricted).",
                        err=True,
                    )
            else:
                if not fetch_tags_in_container_openshift(
                    pod_name, namespace, context=openshift_context,
                ):
                    typer.echo(
                        "Warning: Could not fetch tags from origin "
                        "(network may be restricted).",
                        err=True,
                    )
    else:
        typer.echo("No local origin remote found. Skipping origin setup in container.")

    typer.echo("Git setup complete.")
    return True


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
            session = backend_obj.get_session(name)
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
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        # Get namespace
        if os_config.namespace:
            namespace = os_config.namespace
        else:
            try:
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


def _resolve_backend_for_domains(
    name: str,
    backend: BackendType | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> Backend:
    """Resolve the backend instance for allowed-domains command.

    Args:
        name: Session name.
        backend: Explicit backend type, or None for auto-detect.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Backend instance.

    Raises:
        typer.Exit: If session not found or backend not supported.
    """
    if backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        _, backend_obj = result
        return backend_obj

    if backend == BackendType.podman:
        return PodmanBackend()

    openshift_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
    )
    return OpenShiftBackend(config=openshift_config)


def _check_domains_mutual_exclusivity(
    add: list[str] | None,
    remove: list[str] | None,
    replace: list[str] | None,
) -> None:
    """Check that at most one of --add, --remove, --replace is specified.

    Raises:
        typer.Exit: If more than one is specified.
    """
    specified = sum(1 for opt in (add, remove, replace) if opt)
    if specified > 1:
        typer.echo(
            "Error: Only one of --add, --remove, --replace can be specified.",
            err=True,
        )
        raise typer.Exit(1)


def _expand_domains_or_exit(domains: list[str]) -> list[str]:
    """Expand domains and exit if 'all' is used.

    Args:
        domains: Raw domain input list.

    Returns:
        Expanded domain list.

    Raises:
        typer.Exit: If 'all' is specified.
    """
    from paude.domains import expand_domains

    expanded = expand_domains(domains)
    if expanded is None:
        typer.echo(
            "Error: 'all' cannot be used with --add, --remove, or --replace.",
            err=True,
        )
        raise typer.Exit(1)
    return expanded


def _list_domains(backend_obj: Backend, name: str) -> None:
    """List current allowed domains for a session.

    Args:
        backend_obj: Backend instance.
        name: Session name.
    """
    from paude.domains import format_domains_for_display

    domains = backend_obj.get_allowed_domains(name)
    summary = format_domains_for_display(domains)
    typer.echo(f"Network: {summary}")
    if domains is not None:
        typer.echo("")
        for domain in domains:
            typer.echo(f"  {domain}")


def _add_domains(backend_obj: Backend, name: str, add: list[str]) -> None:
    """Add domains to the current allowed list.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        add: Domains to add.
    """
    expanded = _expand_domains_or_exit(add)
    current = backend_obj.get_allowed_domains(name)
    if current is None:
        typer.echo(
            "Error: Session has unrestricted network (no proxy). "
            "Cannot add domains.",
            err=True,
        )
        raise typer.Exit(1)

    # Merge with dedup, preserving order
    seen = set(current)
    merged = list(current)
    for d in expanded:
        if d not in seen:
            merged.append(d)
            seen.add(d)

    backend_obj.update_allowed_domains(name, merged)
    added_count = len(merged) - len(current)
    typer.echo(f"Added {added_count} domain(s) to session '{name}'.")


def _remove_domains(backend_obj: Backend, name: str, remove: list[str]) -> None:
    """Remove domains from the current allowed list.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        remove: Domains to remove.
    """
    expanded = _expand_domains_or_exit(remove)
    current = backend_obj.get_allowed_domains(name)
    if current is None:
        typer.echo(
            "Error: Session has unrestricted network (no proxy). "
            "Cannot remove domains.",
            err=True,
        )
        raise typer.Exit(1)

    remove_set = set(expanded)
    remaining = [d for d in current if d not in remove_set]

    if not remaining:
        typer.echo(
            "Error: Cannot remove all domains. "
            "At least one domain must remain.",
            err=True,
        )
        raise typer.Exit(1)

    backend_obj.update_allowed_domains(name, remaining)
    removed_count = len(current) - len(remaining)
    typer.echo(f"Removed {removed_count} domain(s) from session '{name}'.")


def _replace_domains(backend_obj: Backend, name: str, replace: list[str]) -> None:
    """Replace all domains for a session.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        replace: New domain list.
    """
    expanded = _expand_domains_or_exit(replace)
    backend_obj.update_allowed_domains(name, expanded)
    typer.echo(f"Replaced domains for session '{name}' ({len(expanded)} domain(s)).")


@app.command("allowed-domains")
def allowed_domains_cmd(
    name: Annotated[str, typer.Argument(help="Session name.")],
    add: Annotated[
        list[str] | None,
        typer.Option("--add", help="Add domains to current list."),
    ] = None,
    remove: Annotated[
        list[str] | None,
        typer.Option("--remove", help="Remove domains from current list."),
    ] = None,
    replace: Annotated[
        list[str] | None,
        typer.Option("--replace", help="Replace entire domain list."),
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
    """Manage allowed egress domains for a session."""
    _check_domains_mutual_exclusivity(add, remove, replace)

    backend_obj = _resolve_backend_for_domains(
        name, backend, openshift_context, openshift_namespace
    )

    try:
        if add:
            _add_domains(backend_obj, name, add)
        elif remove:
            _remove_domains(backend_obj, name, remove)
        elif replace:
            _replace_domains(backend_obj, name, replace)
        else:
            _list_domains(backend_obj, name)
    except NotImplementedError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except (OpenshiftSessionNotFoundError, SessionNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error managing domains: {e}", err=True)
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
) -> None:
    """Run Claude Code in an isolated container."""
    # If a subcommand is invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    # Bare 'paude' command shows session list
    session_list()
