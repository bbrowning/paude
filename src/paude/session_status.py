"""Session activity detection via tmux state inspection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from paude.backends.base import Backend
from paude.constants import CONTAINER_WORKSPACE


@dataclass
class WorkSummary:
    """Git-derived summary of what a session is working on.

    Attributes:
        branch: Current branch name (or "HEAD" if detached).
        commits_ahead: Number of commits ahead of origin/main.
        latest_subject: Subject of the most recent commit ahead of origin/main.
        changed_files: Basenames of uncommitted changed files (up to 5).
    """

    branch: str
    commits_ahead: int
    latest_subject: str
    changed_files: list[str] = field(default_factory=list)


_COMBINED_QUERY_CMD = (
    "tmux list-windows -t claude"
    " -F '#{window_activity}' 2>/dev/null; true"
    f" && cd {CONTAINER_WORKSPACE}"
    ' && echo "BRANCH:$(git rev-parse --abbrev-ref HEAD'
    ' 2>/dev/null)"'
    ' && echo "AHEAD:$(git rev-list --count'
    ' origin/main..HEAD 2>/dev/null)"'
    ' && echo "SUBJECT:$(git log --oneline -1'
    ' --format=%s origin/main..HEAD 2>/dev/null)"'
    ' && echo "CHANGED:$(git diff --name-only HEAD 2>/dev/null'
    " | head -5 | sed 's|.*/||' | paste -sd,)\""
)


def get_session_enrichment(
    backend: Backend, session_name: str
) -> tuple[SessionActivity, WorkSummary | None]:
    """Query tmux and git state in a single exec call.

    Combines activity and work summary queries to minimize
    remote execution overhead (one exec instead of multiple).

    Args:
        backend: Backend instance with exec_in_session method.
        session_name: Session name.

    Returns:
        Tuple of (SessionActivity, WorkSummary or None).
    """
    rc, output, _ = backend.exec_in_session(session_name, _COMBINED_QUERY_CMD)

    lines = output.strip().splitlines() if rc == 0 else []

    activity_ts = ""
    branch = ""
    ahead = 0
    subject = ""
    changed_files: list[str] = []

    for line in lines:
        if line.startswith("BRANCH:"):
            branch = line[len("BRANCH:") :].strip()
        elif line.startswith("AHEAD:"):
            try:
                ahead = int(line[len("AHEAD:") :].strip())
            except ValueError:
                ahead = 0
        elif line.startswith("SUBJECT:"):
            subject = line[len("SUBJECT:") :].strip()
        elif line.startswith("CHANGED:"):
            raw = line[len("CHANGED:") :].strip()
            changed_files = [f for f in raw.split(",") if f] if raw else []
        elif not activity_ts:
            activity_ts = line.strip()

    activity = parse_activity(activity_ts)
    summary = (
        WorkSummary(
            branch=branch,
            commits_ahead=ahead,
            latest_subject=subject,
            changed_files=changed_files,
        )
        if branch
        else None
    )

    return activity, summary


def format_work_summary(summary: WorkSummary | None, max_width: int = 40) -> str:
    """Format a WorkSummary into a display string.

    When there are commits ahead, shows branch + subject + count.
    When there are no commits but files are changed, shows file names.

    Args:
        summary: WorkSummary to format, or None.
        max_width: Maximum width for the output string.

    Returns:
        Formatted string for display in the status table.
    """
    if summary is None:
        return ""

    if summary.branch == "HEAD":
        return "detached"

    is_default = summary.branch in ("main", "master")

    # Case 1: commits ahead — show branch + subject + count
    if summary.commits_ahead > 0 or summary.latest_subject:
        prefix_parts: list[str] = []
        if not is_default:
            prefix_parts.append(summary.branch)
        if summary.latest_subject:
            prefix_parts.append(summary.latest_subject)

        suffix = f"(+{summary.commits_ahead})" if summary.commits_ahead > 0 else ""

        if not prefix_parts and not suffix:
            return ""

        prefix = " ".join(prefix_parts)
        if prefix and suffix:
            text = f"{prefix} {suffix}"
        else:
            text = prefix or suffix

        if len(text) > max_width:
            suffix_with_space = f" {suffix}" if suffix else ""
            avail = max_width - len(suffix_with_space) - 3  # 3 for "..."
            if avail > 0:
                text = prefix[:avail] + "..." + suffix_with_space
            else:
                text = text[:max_width]

        return text

    # Case 2: no commits but files changed — show file names
    if summary.changed_files:
        return _format_changed_files(summary.changed_files, max_width)

    # Case 3: non-default branch with no commits and no changes
    if not is_default:
        return summary.branch

    return ""


def _format_changed_files(files: list[str], max_width: int) -> str:
    """Format changed file names for display.

    Format: "editing: file1.py, file2.py (+N)" where N is remaining count.
    """
    prefix = "editing: "
    total = len(files)
    shown: list[str] = []

    for f in files:
        shown.append(f)
        remaining = total - len(shown)
        suffix = f" (+{remaining})" if remaining > 0 else ""
        text = prefix + ", ".join(shown) + suffix
        if len(text) > max_width and len(shown) > 1:
            shown.pop()
            break

    remaining = total - len(shown)
    suffix = f" (+{remaining})" if remaining > 0 else ""
    return prefix + ", ".join(shown) + suffix


@dataclass
class SessionActivity:
    """Activity information for a running session.

    Attributes:
        last_activity: Human-readable time since last activity (e.g., "2m ago").
        state: Session state ("Working", "Idle", "Waiting for input", "Stopped").
        elapsed_seconds: Seconds since last activity, or None if unknown.
    """

    last_activity: str
    state: str
    elapsed_seconds: int | None = None


_TMUX_QUERY_CMD = (
    "tmux list-windows -t claude -F '#{window_activity}' 2>/dev/null; true"
)


def get_session_activity(backend: Backend, session_name: str) -> SessionActivity:
    """Query tmux state in a running session.

    Args:
        backend: Backend instance with exec_in_session method.
        session_name: Session name.

    Returns:
        SessionActivity with parsed state and timing.
    """
    rc, output, _ = backend.exec_in_session(session_name, _TMUX_QUERY_CMD)

    activity_ts = output.strip() if rc == 0 else ""
    return parse_activity(activity_ts)


def parse_activity(activity_timestamp: str) -> SessionActivity:
    """Parse tmux activity timestamp into human-readable state.

    Args:
        activity_timestamp: Unix timestamp string from tmux window_activity.

    Returns:
        SessionActivity with parsed state.
    """
    elapsed = _parse_elapsed_seconds(activity_timestamp)
    last_activity = _format_elapsed(elapsed)
    state = _detect_state(elapsed)
    return SessionActivity(
        last_activity=last_activity, state=state, elapsed_seconds=elapsed
    )


def _parse_elapsed_seconds(timestamp_str: str) -> int | None:
    """Parse a unix timestamp string into elapsed seconds since now.

    Returns:
        Elapsed seconds (may be negative if timestamp is in the future),
        or None if unparseable.
    """
    try:
        ts = int(timestamp_str.strip().split("\n")[0])
    except (ValueError, IndexError):
        return None
    return int(time.time()) - ts


def _format_elapsed(elapsed: int | None) -> str:
    """Format elapsed seconds as human-readable time (e.g., '2m ago').

    Args:
        elapsed: Seconds since last activity, or None if unknown.

    Returns:
        Human-readable elapsed time string, or "unknown" if None.
    """
    if elapsed is None:
        return "unknown"
    if elapsed < 0:
        return "just now"
    if elapsed < 60:
        return f"{elapsed}s ago"
    if elapsed < 3600:
        return f"{elapsed // 60}m ago"
    if elapsed < 86400:
        return f"{elapsed // 3600}h ago"
    return f"{elapsed // 86400}d ago"


def _detect_state(elapsed: int | None) -> str:
    """Detect session state from elapsed seconds.

    Active if activity within the last 2 minutes, Idle otherwise.
    """
    if elapsed is None:
        return "Idle"
    if elapsed < 120:
        return "Active"
    return "Idle"
