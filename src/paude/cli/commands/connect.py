"""Session connect command."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _get_backend_instance,
    find_session_backend,
)
from paude.session_discovery import resolve_session_for_backend


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
) -> None:
    """Attach to a running session."""
    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name)
        if result:
            backend, backend_obj = result
            exit_code = backend_obj.connect_session(name)
            raise typer.Exit(exit_code)
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        session, backend_obj = _auto_select_session(
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
        exit_code = backend_obj.connect_session(session.name)
        raise typer.Exit(exit_code)

    # Backend specified explicitly
    backend_instance = _get_backend_instance(backend)  # type: ignore[arg-type]
    if not name:
        name = resolve_session_for_backend(backend_instance, status_filter="running")
        if not name:
            raise typer.Exit(1)

    exit_code = backend_instance.connect_session(name)
    raise typer.Exit(exit_code)
