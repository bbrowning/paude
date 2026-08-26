"""Status, reset, and harvest commands."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.cli.app import app


@app.command("status")
def status_cmd(
    session: Annotated[
        str | None,
        typer.Argument(help="Session name (all sessions if not specified)"),
    ] = None,
) -> None:
    """Show enriched status for all sessions."""
    from paude.workflow import status_sessions

    status_sessions(session_name=session)


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
) -> None:
    """Reset a session's workspace for a new task."""
    from paude.workflow import reset_session

    reset_session(
        session_name=session,
        branch=branch,
        force=force,
        keep_conversation=keep_conversation,
    )


@app.command("harvest")
def harvest_cmd(
    session: Annotated[str, typer.Argument(help="Session name to harvest from.")],
    branch: Annotated[
        str | None,
        typer.Option(
            "--branch",
            "-b",
            help="Local branch name to create (defaults to --from).",
        ),
    ] = None,
    source_branch: Annotated[
        str | None,
        typer.Option(
            "--from",
            "--source-branch",
            help="Branch or ref to harvest from the container.",
        ),
    ] = None,
    pr: Annotated[
        bool,
        typer.Option("--pr", help="Create a PR after harvesting."),
    ] = False,
    pr_title: Annotated[
        str | None,
        typer.Option("--pr-title", help="PR title (defaults to branch name)."),
    ] = None,
    container_path: Annotated[
        str | None,
        typer.Option(
            "--container-path",
            help=(
                "Path of the repo inside the container to harvest from "
                "(default: inferred from a matching remote or the session "
                "workspace)."
            ),
        ),
    ] = None,
    remote: Annotated[
        str | None,
        typer.Option(
            "--remote",
            help="Git remote name to use (default: paude-<session>).",
        ),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help=(
                "Host git repo to harvest into "
                "(default: the session's recorded workspace)."
            ),
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
        container_path=container_path,
        remote_name=remote,
        repo=repo,
        source_branch=source_branch,
    )
