"""Session cp command."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.base import Backend
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _parse_copy_path,
    find_session_backend,
)


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
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error copying: {e}", err=True)
        raise typer.Exit(1) from None
