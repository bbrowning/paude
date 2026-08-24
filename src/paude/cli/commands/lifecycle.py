"""Session start and stop commands."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _get_backend_instance,
    _parse_forward_ports,
    called_process_stderr,
    find_session_backend,
)
from paude.session_discovery import resolve_session_for_backend


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
    forward_port: Annotated[
        list[str] | None,
        typer.Option(
            "--forward-port",
            help=(
                "Forward a container port to the host for this session. "
                "Repeatable. Accepts PORT (same on both), HOST:CONTAINER, or "
                "HOST_IP:HOST:CONTAINER. Binds 127.0.0.1 by default."
            ),
        ),
    ] = None,
) -> None:
    """Start a session and connect to it."""
    forward_ports = _parse_forward_ports(forward_port)

    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name)
        if result:
            backend, backend_obj = result
            try:
                exit_code = backend_obj.start_session(name, forward_ports)
                raise typer.Exit(exit_code)
            except typer.Exit:
                # A clean start raises typer.Exit(exit_code); let it propagate
                # instead of being caught by the broad handler below and
                # reported as a failure.
                raise
            except Exception as e:
                typer.echo(f"Error starting session: {e}", err=True)
                if detail := called_process_stderr(e):
                    typer.echo(detail, err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    # If no name and no backend specified, search all backends
    if not name and backend is None:
        session, backend_obj = _auto_select_session(
            no_sessions_hints=[
                "No sessions found.",
                "",
                "To create and start a session:",
                "  paude create && paude start",
            ],
            multi_hint_format="  paude start {name}  # {backend_type}, {status}",
        )
        typer.echo(f"Starting '{session.name}' ({session.backend_type})...")
        exit_code = backend_obj.start_session(session.name, forward_ports)
        raise typer.Exit(exit_code)

    # Backend specified explicitly
    backend_instance = _get_backend_instance(backend)  # type: ignore[arg-type]
    if not name:
        name = resolve_session_for_backend(backend_instance)
        if not name:
            raise typer.Exit(1)

    try:
        exit_code = backend_instance.start_session(name, forward_ports)
        raise typer.Exit(exit_code)
    except typer.Exit:
        # A clean start raises typer.Exit(exit_code); let it propagate instead
        # of being caught by the broad handler below and reported as a failure.
        raise
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error starting session: {e}", err=True)
        if detail := called_process_stderr(e):
            typer.echo(detail, err=True)
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
) -> None:
    """Stop a session (preserves data)."""
    # Auto-detect backend if name is provided but backend is not
    if name and backend is None:
        result = find_session_backend(name)
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
            status_filter="running",
            no_sessions_hints=["No running sessions to stop."],
            multi_hint_format="  paude stop {name}  # {backend_type}",
        )
        typer.echo(f"Stopping '{session.name}' ({session.backend_type})...")
        backend_obj.stop_session(session.name)
        typer.echo(f"Session '{session.name}' stopped.")
        return

    # Backend specified explicitly
    backend_instance = _get_backend_instance(backend)  # type: ignore[arg-type]
    if not name:
        name = resolve_session_for_backend(backend_instance, status_filter="running")
        if not name:
            raise typer.Exit(1)

    try:
        backend_instance.stop_session(name)
        typer.echo(f"Session '{name}' stopped.")
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error stopping session: {e}", err=True)
        raise typer.Exit(1) from None
