"""Session list command."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.cli.app import BackendType, app


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

    from paude import __version__

    # Print header
    typer.echo(
        f"{'NAME':<25} {'BACKEND':<12} {'STATUS':<12} {'VERSION':<12} {'WORKSPACE':<40}"
    )
    typer.echo("-" * 102)

    for session in all_sessions:
        workspace_str = str(session.workspace)
        if len(workspace_str) > 40:
            workspace_str = "..." + workspace_str[-37:]
        version_str = session.version or "-"
        if session.version and session.version != __version__:
            version_str += "*"
        line = (
            f"{session.name:<25} {session.backend_type:<12} "
            f"{session.status:<12} {version_str:<12} {workspace_str:<40}"
        )
        typer.echo(line)
