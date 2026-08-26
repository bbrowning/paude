"""Workflow commands for paude sessions."""

from __future__ import annotations

import fnmatch
import shlex
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer

from paude.backends.base import Backend, Session
from paude.backends.naming import (
    engine_binary_for_backend,
    resource_name,
)
from paude.constants import BASE_REF_NAME, CONTAINER_HOME, CONTAINER_WORKSPACE

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
    connect_timeout: int | None = None,
) -> tuple[str, Backend, Session]:
    """Find the backend and session. Raises typer.Exit if not found."""
    from paude.cli import find_session_backend

    result = find_session_backend(
        session_name,
        connect_timeout=connect_timeout,
    )
    if result is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    backend_type, backend = result[0], result[1]
    session = backend.get_session(session_name)
    if session is None:
        typer.echo(f"Error: Session '{session_name}' not found.", err=True)
        raise typer.Exit(1)

    return backend_type, backend, session


def _remote_targets_container_path(remote_url: str, container_path: str) -> bool:
    """Whether a paude ext:: remote URL points at ``container_path``.

    paude remote URLs end with ``%S <container_path>``, so the container path is
    the final whitespace-delimited token. URLs without the ``%S`` marker are not
    paude ext:: remotes; treat those as a match so a manually configured remote
    isn't blocked.
    """
    parsed = _parse_paude_remote_url(remote_url)
    if parsed is None:
        return True
    return parsed[1] == container_path


def _parse_paude_remote_url(remote_url: str) -> tuple[str, str] | None:
    """Return the container name and path encoded in a paude ext URL."""
    if not remote_url.startswith("ext::"):
        return None

    command, marker, container_path = remote_url[6:].partition(" %S ")
    if not marker or not container_path:
        return None

    try:
        command_parts = shlex.split(command)
        exec_index = command_parts.index("exec")
    except ValueError:
        return None

    container_index = exec_index + 1
    while container_index < len(command_parts) and command_parts[
        container_index
    ].startswith("-"):
        container_index += 1
    if container_index >= len(command_parts):
        return None
    return command_parts[container_index], container_path


def _session_remote_candidates(
    session_name: str,
    cwd: Path,
    container_path: str | None,
) -> list[tuple[str, str]]:
    """Find current-repository ext remotes targeting a session."""
    from paude.git_remote import list_git_remotes

    candidates: list[tuple[str, str]] = []
    for remote_name, remote_url in list_git_remotes(cwd=cwd):
        parsed = _parse_paude_remote_url(remote_url)
        if parsed is None or parsed[0] != resource_name(session_name):
            continue
        if container_path is None or parsed[1] == container_path:
            candidates.append((remote_name, parsed[1]))
    return candidates


def _current_git_repo() -> Path | None:
    """Return the repository containing the current working directory."""
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _resolve_harvest_target(
    session_name: str,
    session: Session,
    remote_name: str | None,
    container_path: str | None,
    repo: str | None,
) -> tuple[Path, str | None, str]:
    """Resolve host repo, remote, and container path for harvest."""
    from paude.git_remote import git_remote_exists, git_remote_get_url

    current_repo = _current_git_repo()
    workspace = Path(repo).expanduser().resolve() if repo else session.workspace
    selected_remote = remote_name
    selected_path = container_path

    if not repo and current_repo is not None:
        if remote_name and git_remote_exists(remote_name, cwd=current_repo):
            workspace = current_repo
        elif remote_name is None:
            candidates = _session_remote_candidates(
                session_name, current_repo, container_path
            )
            if len(candidates) > 1:
                details = ", ".join(
                    f"{name} ({path})" for name, path in candidates
                )
                typer.echo(
                    f"Error: Multiple remotes in the current repository target "
                    f"session '{session_name}': {details}.",
                    err=True,
                )
                typer.echo(
                    "  Use --remote to choose one, or --repo to choose a checkout.",
                    err=True,
                )
                raise typer.Exit(1)
            if candidates:
                workspace = current_repo
                selected_remote, selected_path = candidates[0]

    if selected_path is None:
        candidate_name = selected_remote or resource_name(session_name)
        if (workspace / ".git").exists() and git_remote_exists(
            candidate_name, cwd=workspace
        ):
            remote_url = git_remote_get_url(candidate_name, cwd=workspace)
            parsed = _parse_paude_remote_url(remote_url or "")
            if parsed is not None:
                selected_path = parsed[1]

    return workspace, selected_remote, selected_path or CONTAINER_WORKSPACE


def _ensure_remote_exists(
    session_name: str,
    backend_type: str,
    remote_name: str | None = None,
    container_path: str = CONTAINER_WORKSPACE,
    cwd: Path | None = None,
) -> str:
    """Ensure a git remote exists in ``cwd``, auto-adding if needed.

    ``remote_name`` defaults to ``paude-<session>``; ``container_path`` selects
    which repo path inside the container the remote points at. The container name
    is always ``paude-<session>`` regardless of the remote name.

    If a remote by the resolved name already exists but points at a different
    container path than requested, raise typer.Exit rather than silently
    harvesting the wrong repo (the default remote name does not encode the path).
    """
    from paude.cli.remote_git_setup import _prepare_session_git_remote
    from paude.git_remote import (
        enable_ext_protocol,
        git_remote_add,
        git_remote_exists,
        git_remote_get_url,
        is_ext_protocol_allowed,
    )

    container_name = resource_name(session_name)
    resolved_remote = remote_name or container_name

    if git_remote_exists(resolved_remote, cwd=cwd):
        existing_url = git_remote_get_url(resolved_remote, cwd=cwd)
        if existing_url and not _remote_targets_container_path(
            existing_url, container_path
        ):
            typer.echo(
                f"Error: Remote '{resolved_remote}' already exists but points at "
                f"a different container path than '{container_path}'.",
                err=True,
            )
            typer.echo(f"  Existing remote: {existing_url}", err=True)
            typer.echo(
                "  Use --remote to pick a distinct remote name, or remove the "
                "existing remote first.",
                err=True,
            )
            raise typer.Exit(1)
        return resolved_remote

    typer.echo(f"Adding git remote '{resolved_remote}'...", err=True)

    if not is_ext_protocol_allowed(cwd=cwd):
        if not enable_ext_protocol(cwd=cwd):
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            raise typer.Exit(1)

    engine = engine_binary_for_backend(backend_type)
    remote_url, _transport = _prepare_session_git_remote(
        session_name, container_name, engine, workspace_path=container_path
    )

    if not git_remote_add(resolved_remote, remote_url, cwd=cwd):
        typer.echo(f"Error: Failed to add remote '{resolved_remote}'.", err=True)
        raise typer.Exit(1)

    return resolved_remote


def _verify_container_repo(
    backend: Backend,
    session_name: str,
    container_path: str,
) -> None:
    """Ensure ``container_path`` is an existing git repo, else exit with an error.

    Harvest reads from a repo the agent already created; without this guard a
    mistyped ``--container-path`` would fall through to the workspace-init
    ``git init`` and silently create a stray empty repo at the wrong path, then
    fail later with a confusing ref error.
    """
    rc, _stdout, _stderr = backend.exec_in_session(
        session_name,
        f"git -C {shlex.quote(container_path)} rev-parse --is-inside-work-tree",
    )
    if rc != 0:
        typer.echo(
            f"Error: No git repository found at container path '{container_path}'.",
            err=True,
        )
        typer.echo(
            "  Check that the path is correct and the container is running.",
            err=True,
        )
        raise typer.Exit(1)


def _get_container_branch(
    backend: Backend,
    session_name: str,
    container_path: str = CONTAINER_WORKSPACE,
) -> str:
    """Query the current branch inside a session's container."""
    rc, stdout, stderr = backend.exec_in_session(
        session_name,
        f"git -C {shlex.quote(container_path)} rev-parse --abbrev-ref HEAD",
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
    branch_name: str | None = None,
    create_pr: bool = False,
    pr_title: str | None = None,
    container_path: str | None = None,
    remote_name: str | None = None,
    repo: str | None = None,
    source_branch: str | None = None,
) -> None:
    """Harvest changes from a running session into a local branch.

    ``source_branch`` selects a branch or ref in the container without requiring
    it to be checked out. When omitted, the checked-out container branch is
    used. ``branch_name`` defaults to the source branch when one is supplied.
    ``container_path`` selects which repo path inside the container to harvest
    from, ``remote_name`` the git remote to use, and ``repo`` the host repo to
    harvest into. Existing matching remotes in the current host checkout can
    supply the latter two defaults.
    """
    from paude.git_remote import git_diff_stat, git_fetch_from_remote

    if branch_name is None:
        if source_branch is None:
            typer.echo(
                "Error: --branch is required unless --from is supplied.", err=True
            )
            raise typer.Exit(1)
        if source_branch.startswith("refs/heads/"):
            branch_name = source_branch.removeprefix("refs/heads/")
        else:
            branch_name = source_branch.removeprefix("refs/")

    _validate_harvest_branch(branch_name)

    backend_type, backend, session = _find_backend_and_session(session_name)

    workspace, remote_name, container_path = _resolve_harvest_target(
        session_name,
        session,
        remote_name,
        container_path,
        repo,
    )
    if not (workspace / ".git").is_dir():
        typer.echo(
            f"Error: Workspace '{workspace}' is not a git repository "
            f"(missing or no .git directory).",
            err=True,
        )
        raise typer.Exit(1)

    _verify_container_repo(backend, session_name, container_path)

    remote_name = _ensure_remote_exists(
        session_name,
        backend_type,
        remote_name=remote_name,
        container_path=container_path,
        cwd=workspace,
    )

    if source_branch is None:
        source_ref = _get_container_branch(backend, session_name, container_path)
        fetch_ref = None
        remote_ref = f"{remote_name}/{source_ref}"
        typer.echo(f"Container is on branch '{source_ref}'.", err=True)
    else:
        source_ref = source_branch
        if source_ref.startswith("refs/"):
            fetch_ref = source_ref
            remote_ref = "FETCH_HEAD"
        else:
            fetch_ref = None
            remote_ref = f"{remote_name}/{source_ref}"
        typer.echo(f"Using container source ref '{source_branch}'.", err=True)

    typer.echo(f"Fetching from '{remote_name}'...", err=True)
    if fetch_ref is None:
        fetch_succeeded = git_fetch_from_remote(remote_name, cwd=workspace)
    else:
        fetch_succeeded = git_fetch_from_remote(
            remote_name, cwd=workspace, source_ref=fetch_ref
        )
    if not fetch_succeeded:
        typer.echo("Error: Failed to fetch from remote.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Resetting '{branch_name}' to '{remote_ref}'...", err=True)
    result = subprocess.run(
        ["git", "checkout", "-B", branch_name, remote_ref],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    if result.returncode != 0:
        typer.echo(
            f"Error: Failed to harvest source ref '{source_ref}' from "
            f"remote '{remote_name}': {result.stderr.strip()}",
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
    session_name: str | None = None,
) -> None:
    """Display enriched status for all sessions, or a single named session."""
    from paude.session_status import (
        SessionActivity,
        WorkSummary,
        format_work_summary,
        get_session_enrichment,
    )

    if session_name:
        from paude.transport.ssh import SSH_STATUS_TIMEOUT

        _btype, found_backend, found_session = _find_backend_and_session(
            session_name,
            connect_timeout=SSH_STATUS_TIMEOUT,
        )
        all_merged = [found_session]
        backend_by_name: dict[str, Backend] = {found_session.name: found_backend}
    else:
        from paude.registry import SessionRegistry, merge_registry_with_live
        from paude.session_discovery import collect_all_sessions

        live_results, reachable_backends = collect_all_sessions()
        registry = SessionRegistry()
        live_sessions = [s for s, _b in live_results]
        all_merged = merge_registry_with_live(
            registry, live_sessions, reachable_backends
        )
        backend_by_name = {s.name: b for s, b in live_results}

    if not all_merged:
        typer.echo("No sessions found.")
        return

    # Separate running sessions (enrichable) from others
    running_rows: list[
        tuple[Session, str, SessionActivity | None, WorkSummary | None]
    ] = []
    other_rows: list[tuple[Session, str]] = []

    # Enrich running sessions concurrently
    with ThreadPoolExecutor(max_workers=8) as pool:
        enrichment_futures = []
        for session in all_merged:
            if session.status not in ("running", "degraded"):
                other_rows.append((session, session.backend_type))
                continue
            backend = backend_by_name.get(session.name)
            if not backend:
                running_rows.append((session, session.backend_type, None, None))
                continue
            enrichment_futures.append(
                (
                    session,
                    pool.submit(
                        get_session_enrichment,
                        backend,
                        session.name,
                        agent_name=session.agent,
                    ),
                )
            )

        for session, fut in enrichment_futures:
            try:
                activity, summary = fut.result()
            except Exception:  # noqa: S110
                activity, summary = None, None
            running_rows.append((session, session.backend_type, activity, summary))

    def _sort_key(
        r: tuple[Session, str, SessionActivity | None, WorkSummary | None],
    ) -> float:
        activity = r[2]
        if activity and activity.elapsed_seconds is not None:
            return float(activity.elapsed_seconds)
        return float("inf")

    running_rows.sort(key=_sort_key)

    fixed_width = 20 + 15 + 10 + 10 + 10 + 5  # columns + spaces before SUMMARY
    term_width = shutil.get_terminal_size((80, 24)).columns
    summary_width = max(30, term_width - fixed_width)

    cols = (
        f"{'SESSION':<20} {'PROJECT':<15} {'BACKEND':<10} "
        f"{'ACTIVITY':<10} {'STATE':<10} {'SUMMARY'}"
    )
    typer.echo(cols)
    typer.echo("-" * len(cols))

    for session, backend_type, activity, summary in running_rows:
        project = session.workspace.name if session.workspace else ""
        act_str = activity.last_activity if activity else ""
        state_str = activity.state if activity else ""
        summary_str = format_work_summary(summary, max_width=summary_width)

        typer.echo(
            f"{session.name:<20} {project:<15} {backend_type:<10} "
            f"{act_str:<10} {state_str:<10} {summary_str}"
        )

    for session, backend_type in other_rows:
        project = session.workspace.name if session.workspace else ""
        typer.echo(
            f"{session.name:<20} {project:<15} {backend_type:<10} "
            f"{'':<10} {session.status:<10}"
        )


def reset_session(
    session_name: str,
    branch: str = "main",
    force: bool = False,
    keep_conversation: bool = False,
) -> None:
    """Reset a session's workspace for a new task."""
    _backend_type, backend, session = _find_backend_and_session(session_name)

    if session.status != "running":
        typer.echo(
            f"Error: Session '{session_name}' is not running. "
            f"Use 'paude start {session_name}' first.",
            err=True,
        )
        raise typer.Exit(1)

    # Re-resolve origin from the host repo's branch tracking remote
    from paude.git_remote import resolve_origin_cmd

    set_origin_cmd = resolve_origin_cmd(cwd=session.workspace)
    if set_origin_cmd:
        backend.exec_in_session(session_name, set_origin_cmd)

    if not force:
        _check_unmerged_work(backend, session_name, branch)

    typer.echo(f"Resetting workspace to '{branch}'...", err=True)
    quoted_branch = shlex.quote(branch)
    ws = CONTAINER_WORKSPACE
    reset_cmd = (
        f"git -C {ws} fetch origin 2>/dev/null; "
        f"git -C {ws} checkout {quoted_branch} 2>/dev/null; "
        f"git -C {ws} reset --hard origin/{quoted_branch} "
        f"2>/dev/null || "
        f"git -C {ws} reset --hard HEAD; "
        f"git -C {ws} clean -fdx && "
        f"git -C {ws} update-ref {BASE_REF_NAME} HEAD"
    )
    rc, _stdout, stderr = backend.exec_in_session(session_name, reset_cmd)
    if rc != 0:
        typer.echo(
            f"Error: Failed to reset workspace: {stderr.strip()}",
            err=True,
        )
        raise typer.Exit(1)

    if not keep_conversation:
        from paude.agents import get_agent

        agent = get_agent(session.agent, provider=session.provider)
        agent_cfg = agent.config
        config_dir = f"{CONTAINER_HOME}/{agent_cfg.config_dir_name}"

        typer.echo(
            "Clearing conversation history and sending clear command...",
            err=True,
        )
        # Delete conversation history but preserve per-project settings
        # (settings.local.json, CLAUDE.md), then send clear command to agent
        clear_cmd = (
            f"find {config_dir}/projects/ "
            r"\( -name '*.jsonl' -o -name 'sessions-index.json' \) "
            "-delete 2>/dev/null; "
            f"find {config_dir}/projects/ -mindepth 2 -maxdepth 2 -type d "
            "-exec rm -rf {} + 2>/dev/null; "
            f"rm -rf {config_dir}/todos/; "
        )
        if agent_cfg.clear_command:
            clear_cmd += (
                f"tmux send-keys -t {agent_cfg.session_name}"
                f' -l "{agent_cfg.clear_command}"; '
                f"sleep 0.1; "
                f"tmux send-keys -t {agent_cfg.session_name} Enter"
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
