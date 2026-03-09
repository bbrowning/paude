"""Status, reset, and harvest commands."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.cli.app import app


@app.command("status")
def status_cmd(
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
    """Show enriched status for all sessions."""
    from paude.workflow import status_sessions

    status_sessions(
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("reset")
def reset_cmd(
    session: Annotated[str, typer.Argument(help="Session name to reset.")],
    branch: Annotated[
        str,
        typer.Option(
            "--branch",
            "-b",
            help="Branch to reset to (default: main).",
        ),
    ] = "main",
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip unmerged work check."),
    ] = False,
    keep_conversation: Annotated[
        bool,
        typer.Option(
            "--keep-conversation",
            help="Keep Claude conversation history.",
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
    """Reset a session's workspace for a new task."""
    from paude.workflow import reset_session

    reset_session(
        session_name=session,
        branch=branch,
        force=force,
        keep_conversation=keep_conversation,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )


@app.command("harvest")
def harvest_cmd(
    session: Annotated[str, typer.Argument(help="Session name to harvest from.")],
    branch: Annotated[
        str,
        typer.Option("--branch", "-b", help="Local branch name to create."),
    ],
    pr: Annotated[
        bool,
        typer.Option("--pr", help="Create a PR after harvesting."),
    ] = False,
    pr_title: Annotated[
        str | None,
        typer.Option("--pr-title", help="PR title (defaults to branch name)."),
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
    """Harvest changes from a running session into a local branch."""
    from paude.workflow import harvest_session

    harvest_session(
        session_name=session,
        branch_name=branch,
        create_pr=pr,
        pr_title=pr_title,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )
