"""Workflow commands for paude sessions."""

from __future__ import annotations

import fnmatch
import shlex
import subprocess
from pathlib import Path

import typer

from paude.backends.base import Backend, Session
from paude.constants import CONTAINER_HOME

_PROTECTED_BRANCH_PATTERNS = frozenset(
    {
        "main",
        "master",
        "release",
        "release-*",
        "release/*",
    }
)


def _validate_harvest_branch(branch_name: str) -> None:
    """Raise typer.Exit if branch_name is a protected branch."""
    for pattern in _PROTECTED_BRANCH_PATTERNS:
        if fnmatch.fnmatch(branch_name, pattern):
            typer.echo(
                f"Error: Cannot harvest to protected branch '{branch_name}'.",
                err=True,
            )
            raise typer.Exit(1)


def _find_backend_and_session(
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> tuple[str, Backend, Session]:
    """Find the backend and session. Raises typer.Exit if not found."""
    from paude.cli import find_session_backend

    result = find_session_backend(session_name, openshift_context, openshift_namespace)
    if result is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    backend_type, backend = result[0], result[1]
    session = backend.get_session(session_name)
    if session is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    return backend_type, backend, session


def _ensure_remote_exists(
    session_name: str,
    backend_type: str,
    backend: Backend,
    workspace: Path,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> str:
    """Ensure a paude git remote exists, auto-adding if needed."""
    from paude.git_remote import (
        build_openshift_remote_url,
        build_podman_remote_url,
        enable_ext_protocol,
        git_remote_add,
        initialize_container_workspace_openshift,
        initialize_container_workspace_podman,
        is_ext_protocol_allowed,
        list_paude_remotes,
    )

    remote_name = f"paude-{session_name}"

    for name, _url in list_paude_remotes():
        if name == remote_name:
            return remote_name

    typer.echo(f"Adding git remote '{remote_name}'...", err=True)

    if not is_ext_protocol_allowed():
        if not enable_ext_protocol():
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            raise typer.Exit(1)

    container_name = f"paude-{session_name}"

    if backend_type == "openshift":
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        try:
            os_backend = OpenShiftBackend(config=os_config)
            namespace = os_backend.namespace
        except Exception:
            namespace = openshift_namespace or "default"

        pod_name = f"paude-{session_name}-0"
        initialize_container_workspace_openshift(
            pod_name, namespace, context=openshift_context
        )
        remote_url = build_openshift_remote_url(
            pod_name, namespace, context=openshift_context
        )
    else:
        initialize_container_workspace_podman(container_name)
        remote_url = build_podman_remote_url(container_name)

    if not git_remote_add(remote_name, remote_url):
        typer.echo(f"Error: Failed to add remote '{remote_name}'.", err=True)
        raise typer.Exit(1)

    return remote_name


def _get_container_branch(backend: Backend, session_name: str) -> str:
    """Query the current branch inside a session's container."""
    rc, stdout, stderr = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace rev-parse --abbrev-ref HEAD",
    )
    if rc != 0:
        typer.echo(
            f"Error: Failed to get branch from container: {stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)
    return stdout.strip()


def harvest_session(
    session_name: str,
    branch_name: str,
    create_pr: bool = False,
    pr_title: str | None = None,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Harvest changes from a running session into a local branch."""
    from paude.git_remote import git_diff_stat, git_fetch_from_remote

    _validate_harvest_branch(branch_name)

    backend_type, backend, session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )

    workspace = session.workspace
    if not (workspace / ".git").is_dir():
        typer.echo(
            f"Error: Workspace '{workspace}' is not a git repository "
            f"(missing or no .git directory).",
            err=True,
        )
        raise typer.Exit(1)

    remote_name = _ensure_remote_exists(
        session_name,
        backend_type,
        backend,
        workspace,
        openshift_context,
        openshift_namespace,
    )

    container_branch = _get_container_branch(backend, session_name)
    typer.echo(f"Container is on branch '{container_branch}'.", err=True)

    typer.echo(f"Fetching from '{remote_name}'...", err=True)
    if not git_fetch_from_remote(remote_name, cwd=workspace):
        typer.echo("Error: Failed to fetch from remote.", err=True)
        raise typer.Exit(1)

    remote_ref = f"{remote_name}/{container_branch}"
    typer.echo(f"Resetting '{branch_name}' to '{remote_ref}'...", err=True)
    result = subprocess.run(
        ["git", "checkout", "-B", branch_name, remote_ref],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    if result.returncode != 0:
        typer.echo(
            f"Error: Failed to reset branch: {result.stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)

    stat = git_diff_stat("main", branch_name, cwd=workspace)
    if stat:
        typer.echo("")
        typer.echo(stat)

    typer.echo(f"Harvested changes to branch '{branch_name}'.", err=True)

    if create_pr:
        # Fetch origin so --force-with-lease has current ref info
        subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True,
            cwd=workspace,
        )
        typer.echo(f"Pushing '{branch_name}' to origin...", err=True)
        push_result = subprocess.run(
            ["git", "push", "--force-with-lease", "-u", "origin", branch_name],
            cwd=workspace,
        )
        if push_result.returncode != 0:
            typer.echo("Error: Failed to push branch to origin.", err=True)
            raise typer.Exit(1)

        # Check if an open PR already exists for this branch
        view_result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch_name,
                "--state",
                "open",
                "--json",
                "url",
                "-q",
                ".[0].url",
            ],
            capture_output=True,
            text=True,
            cwd=workspace,
        )
        if view_result.returncode == 0 and view_result.stdout.strip():
            pr_url = view_result.stdout.strip()
            typer.echo(f"PR already exists and updated: {pr_url}", err=True)
        else:
            typer.echo("Creating PR...", err=True)
            pr_cmd = ["gh", "pr", "create", "--head", branch_name]
            if pr_title:
                pr_cmd += ["--title", pr_title]
            pr_result = subprocess.run(pr_cmd, cwd=workspace)
            if pr_result.returncode != 0:
                typer.echo("Error: Failed to create PR.", err=True)
                raise typer.Exit(1)


def status_sessions(
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Display enriched status for all sessions."""
    from paude.session_discovery import collect_all_sessions
    from paude.session_status import SessionActivity, get_session_activity

    all_sessions = collect_all_sessions(
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )

    if not all_sessions:
        typer.echo("No sessions found.")
        return

    rows: list[tuple[Session, str, SessionActivity | None]] = []
    for session, backend in all_sessions:
        activity: SessionActivity | None = None
        if session.status == "running":
            try:
                activity = get_session_activity(backend, session.name)
            except Exception:  # noqa: S110
                pass
        rows.append((session, session.backend_type, activity))

    cols = (
        f"{'SESSION':<20} {'PROJECT':<15} {'BACKEND':<10} "
        f"{'STATUS':<10} {'ACTIVITY':<10} {'STATE'}"
    )
    typer.echo(cols)
    typer.echo("-" * len(cols))

    for session, backend_type, activity in rows:
        project = session.workspace.name if session.workspace else ""
        status = session.status
        act_str = activity.last_activity if activity else ""
        if activity:
            state_str = activity.state
        elif status == "stopped":
            state_str = "Stopped"
        else:
            state_str = ""

        typer.echo(
            f"{session.name:<20} {project:<15} {backend_type:<10} "
            f"{status:<10} {act_str:<10} {state_str}"
        )


def reset_session(
    session_name: str,
    branch: str = "main",
    force: bool = False,
    keep_conversation: bool = False,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> None:
    """Reset a session's workspace for a new task."""
    _backend_type, backend, session = _find_backend_and_session(
        session_name, openshift_context, openshift_namespace
    )

    if session.status != "running":
        typer.echo(
            f"Error: Session '{session_name}' is not running. "
            f"Use 'paude start {session_name}' first.",
            err=True,
        )
        raise typer.Exit(1)

    if not force:
        _check_unmerged_work(backend, session_name, branch)

    typer.echo(f"Resetting workspace to '{branch}'...", err=True)
    quoted_branch = shlex.quote(branch)
    reset_cmd = (
        f"git -C /pvc/workspace fetch origin 2>/dev/null; "
        f"git -C /pvc/workspace checkout {quoted_branch} 2>/dev/null; "
        f"git -C /pvc/workspace reset --hard origin/{quoted_branch} "
        f"2>/dev/null || "
        f"git -C /pvc/workspace reset --hard HEAD; "
        f"git -C /pvc/workspace clean -fdx"
    )
    rc, _stdout, stderr = backend.exec_in_session(session_name, reset_cmd)
    if rc != 0:
        typer.echo(
            f"Error: Failed to reset workspace: {stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)

    if not keep_conversation:
        typer.echo("Clearing conversation history and sending /clear...", err=True)
        # Delete conversation history but preserve per-project settings
        # (settings.local.json, CLAUDE.md), then send /clear to Claude
        claude_dir = f"{CONTAINER_HOME}/.claude"
        clear_cmd = (
            f"find {claude_dir}/projects/ "
            r"\( -name '*.jsonl' -o -name 'sessions-index.json' \) "
            "-delete 2>/dev/null; "
            f"find {claude_dir}/projects/ -mindepth 2 -maxdepth 2 -type d "
            "-exec rm -rf {} + 2>/dev/null; "
            f"rm -rf {claude_dir}/todos/; "
            'tmux send-keys -t claude "/clear" Enter'
        )
        backend.exec_in_session(session_name, clear_cmd)

    typer.echo(f"Session '{session_name}' reset to '{branch}'.", err=True)


def _check_unmerged_work(
    backend: Backend,
    session_name: str,
    branch: str = "main",
) -> None:
    """Check if session has unmerged work and warn the user."""
    # Fetch origin and check if HEAD is an ancestor of origin/<branch>
    rc, _, _ = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace fetch origin 2>/dev/null"
        f" && git -C /pvc/workspace merge-base --is-ancestor HEAD origin/{branch}",
    )
    if rc == 0:
        # HEAD is already in origin/main — nothing unmerged
        return

    # There's diverged work — get latest commit for the warning message
    rc, stdout, _ = backend.exec_in_session(
        session_name,
        "git -C /pvc/workspace log --oneline -1 HEAD",
    )
    latest = stdout.strip() if rc == 0 else "unknown"
    typer.echo("Warning: Session has work that may not be harvested.", err=True)
    typer.echo(f"  Latest commit: {latest}", err=True)
    typer.echo(
        "  Use --force to skip this check, or 'paude harvest' first.",
        err=True,
    )
    raise typer.Exit(1)
