"""Session connect command."""

from __future__ import annotations

import os
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
