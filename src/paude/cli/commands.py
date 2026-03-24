"""Session commands: delete, start, stop, connect, list, cp."""

from __future__ import annotations

import os
from typing import Annotated

import typer

from paude.backends import (
    SessionNotFoundError,
)
from paude.backends.base import Backend
from paude.backends.openshift import (
    SessionNotFoundError as OpenshiftSessionNotFoundError,
)
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _get_backend_instance,
    _parse_copy_path,
    find_session_backend,
)
from paude.registry import RegistryEntry
from paude.session_discovery import (
    resolve_session_for_backend,
)


def _cleanup_remote_config_dir(entry: RegistryEntry | None) -> None:
    """Best-effort cleanup of remote config temp dir from registry entry."""
    if not entry or not entry.remote_config_dir or not entry.ssh_host:
        return
    try:
        from paude.cli.remote_git_setup import _build_transport
        from paude.transport.config_sync import cleanup_remote_configs

        transport = _build_transport(entry.ssh_host, entry.ssh_key)
        if transport:
            cleanup_remote_configs(transport, entry.remote_config_dir)
    except Exception:  # noqa: S110 - best-effort cleanup
        pass


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
    from paude.cli.remote import _cleanup_session_git_remote, _get_session_workspace

    if not confirm:
        typer.echo(
            f"Deleting session '{name}' will permanently remove all data.",
            err=True,
        )
        typer.echo("Use --confirm to proceed.", err=True)
        raise typer.Exit(1)

    from paude.registry import SessionRegistry

    registry = SessionRegistry()

    # Auto-detect backend if not specified
    if backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            workspace = _get_session_workspace(backend_obj, name)
            try:
                reg_entry = registry.get(name)
                _cleanup_remote_config_dir(reg_entry)
                backend_obj.delete_session(name, confirm=True)
                registry.unregister(name)
                typer.echo(f"Session '{name}' deleted.")
                _cleanup_session_git_remote(name, workspace)
                return
            except Exception as e:
                typer.echo(f"Error deleting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    backend_instance = _get_backend_instance(
        backend, openshift_context, openshift_namespace
    )
    workspace = _get_session_workspace(backend_instance, name)
    try:
        reg_entry = registry.get(name)
        _cleanup_remote_config_dir(reg_entry)
        backend_instance.delete_session(name, confirm=True)
        registry.unregister(name)
        typer.echo(f"Session '{name}' deleted.")
        _cleanup_session_git_remote(name, workspace)
    except (SessionNotFoundError, OpenshiftSessionNotFoundError) as e:
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
        session, backend_obj = _auto_select_session(
            openshift_context,
            openshift_namespace,
            no_sessions_hints=[
                "No sessions found.",
                "",
                "To create and start a session:",
                "  paude create && paude start",
            ],
            multi_hint_format="  paude start {name}  # {backend_type}, {status}",
        )
        typer.echo(f"Starting '{session.name}' ({session.backend_type})...")
        exit_code = backend_obj.start_session(session.name, github_token=resolved_token)
        raise typer.Exit(exit_code)

    # Backend specified explicitly
    backend_instance = _get_backend_instance(
        backend,  # type: ignore[arg-type]
        openshift_context,
        openshift_namespace,
    )
    if not name:
        name = resolve_session_for_backend(backend_instance)
        if not name:
            raise typer.Exit(1)

    try:
        exit_code = backend_instance.start_session(name, github_token=resolved_token)
        raise typer.Exit(exit_code)
    except (SessionNotFoundError, OpenshiftSessionNotFoundError) as e:
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
        session, backend_obj = _auto_select_session(
            openshift_context,
            openshift_namespace,
            status_filter="running",
            no_sessions_hints=["No running sessions to stop."],
            multi_hint_format="  paude stop {name}  # {backend_type}",
        )
        typer.echo(f"Stopping '{session.name}' ({session.backend_type})...")
        backend_obj.stop_session(session.name)
        typer.echo(f"Session '{session.name}' stopped.")
        return

    # Backend specified explicitly
    backend_instance = _get_backend_instance(
        backend,  # type: ignore[arg-type]
        openshift_context,
        openshift_namespace,
    )
    if not name:
        name = resolve_session_for_backend(backend_instance, status_filter="running")
        if not name:
            raise typer.Exit(1)

    try:
        backend_instance.stop_session(name)
        typer.echo(f"Session '{name}' stopped.")
    except (SessionNotFoundError, OpenshiftSessionNotFoundError) as e:
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
        session, backend_obj = _auto_select_session(
            openshift_context,
            openshift_namespace,
            status_filter="running",
            no_sessions_hints=[
                "No running sessions to connect to.",
                "",
                "To see all sessions:",
                "  paude list",
                "",
                "To start a session:",
                "  paude start",
            ],
            multi_hint_format="  paude connect {name}  # {backend_type}, {workspace}",
        )
        typer.echo(f"Connecting to '{session.name}' ({session.backend_type})...")
        exit_code = backend_obj.connect_session(
            session.name, github_token=resolved_token
        )
        raise typer.Exit(exit_code)

    # Backend specified explicitly
    backend_instance = _get_backend_instance(
        backend,  # type: ignore[arg-type]
        openshift_context,
        openshift_namespace,
    )
    if not name:
        name = resolve_session_for_backend(backend_instance, status_filter="running")
        if not name:
            raise typer.Exit(1)

    exit_code = backend_instance.connect_session(name, github_token=resolved_token)
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
    from paude.registry import SessionRegistry, merge_registry_with_live
    from paude.session_discovery import collect_all_sessions

    live_results, reachable_backends = collect_all_sessions(
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        skip_podman=backend == BackendType.openshift,
        skip_openshift=backend == BackendType.podman,
    )
    live_sessions = [s for s, _b in live_results]

    registry = SessionRegistry()
    all_sessions = merge_registry_with_live(registry, live_sessions, reachable_backends)

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
        workspace_str = str(session.workspace)
        if len(workspace_str) > 40:
            workspace_str = "..." + workspace_str[-37:]
        line = (
            f"{session.name:<25} {session.backend_type:<12} "
            f"{session.status:<12} {workspace_str:<40}"
        )
        typer.echo(line)


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
        session_obj, backend_obj = _auto_select_session(
            openshift_context,
            openshift_namespace,
            status_filter="running",
            no_sessions_hints=["No running sessions found."],
            multi_hint_format="  paude cp ... {name}:path",
        )
        session_name = session_obj.name

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
