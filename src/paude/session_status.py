"""Session activity detection via tmux state inspection."""

from __future__ import annotations

import time
from dataclasses import dataclass

from paude.backends.base import Backend


@dataclass
class SessionActivity:
    """Activity information for a running session.

    Attributes:
        last_activity: Human-readable time since last activity (e.g., "2m ago").
        state: Session state ("Working", "Idle", "Waiting for input", "Stopped").
    """

    last_activity: str
    state: str


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
    last_activity = _format_elapsed(activity_timestamp)
    state = _detect_state(activity_timestamp)
    return SessionActivity(last_activity=last_activity, state=state)


def _format_elapsed(timestamp_str: str) -> str:
    """Format a unix timestamp as elapsed time (e.g., '2m ago').

    Args:
        timestamp_str: Unix timestamp as string.

    Returns:
        Human-readable elapsed time string, or "unknown" if unparseable.
    """
    try:
        ts = int(timestamp_str.strip().split("\n")[0])
    except (ValueError, IndexError):
        return "unknown"

    elapsed = int(time.time()) - ts
    if elapsed < 0:
        return "just now"
    if elapsed < 60:
        return f"{elapsed}s ago"
    if elapsed < 3600:
        return f"{elapsed // 60}m ago"
    if elapsed < 86400:
        return f"{elapsed // 3600}h ago"
    return f"{elapsed // 86400}d ago"


def _detect_state(timestamp_str: str) -> str:
    """Detect session state from tmux activity timestamp.

    Uses only the timestamp — no terminal content heuristics.
    Active if activity within the last 2 minutes, Idle otherwise.
    """
    try:
        ts = int(timestamp_str.strip().split("\n")[0])
    except (ValueError, IndexError):
        return "Idle"

    elapsed = int(time.time()) - ts
    if elapsed < 120:
        return "Active"
    return "Idle"
