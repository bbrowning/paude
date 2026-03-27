"""Session start and stop commands."""

from __future__ import annotations

import os
from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _auto_select_session,
    _get_backend_instance,
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
    except SessionNotFoundError as e:
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
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error stopping session: {e}", err=True)
        raise typer.Exit(1) from None
