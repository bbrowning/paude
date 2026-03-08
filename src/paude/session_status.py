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


TMUX_SEPARATOR = "---PAUDE_TMUX_SEP---"

_TMUX_QUERY_CMD = (
    f"tmux list-windows -t claude -F '#{{window_activity}}' 2>/dev/null; "
    f"echo '{TMUX_SEPARATOR}'; "
    "tmux capture-pane -t claude:0.0 -p -S -5 2>/dev/null; true"
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

    activity_ts = ""
    pane_text = ""
    if rc == 0 and TMUX_SEPARATOR in output:
        parts = output.split(TMUX_SEPARATOR, 1)
        activity_ts = parts[0].strip()
        pane_text = parts[1] if len(parts) > 1 else ""

    return parse_activity(activity_ts, pane_text)


def parse_activity(activity_timestamp: str, pane_content: str) -> SessionActivity:
    """Parse tmux output into human-readable state.

    Args:
        activity_timestamp: Unix timestamp string from tmux window_activity.
        pane_content: Last lines of terminal output from tmux capture-pane.

    Returns:
        SessionActivity with parsed state.
    """
    # Parse timestamp
    last_activity = _format_elapsed(activity_timestamp)

    # Determine state from pane content
    state = _detect_state(activity_timestamp, pane_content)

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


def _detect_state(timestamp_str: str, pane_content: str) -> str:
    """Detect session state from tmux output.

    Heuristics:
    - Waiting for input: prompt patterns (plan approval, y/n, permission)
    - Idle: prompt character on last non-empty line + low activity
    - Working: recent activity with non-prompt content
    """
    # Check for waiting-for-input patterns
    lower_content = pane_content.lower()
    waiting_patterns = [
        "approve",
        "[y/n]",
        "(y/n)",
        "y/n?",
        "do you want",
        "permission",
        "confirm",
    ]
    for pattern in waiting_patterns:
        if pattern in lower_content:
            return "Waiting for input"

    # Get last non-empty line
    lines = [line for line in pane_content.strip().split("\n") if line.strip()]
    if not lines:
        return "Idle"

    last_line = lines[-1].strip()

    # Check for prompt characters (idle at shell/claude prompt)
    prompt_chars = [">", "❯", "$", "%", ">>>"]
    if any(last_line.endswith(ch) for ch in prompt_chars):
        return "Idle"

    # Check activity recency
    try:
        ts = int(timestamp_str.strip().split("\n")[0])
        elapsed = int(time.time()) - ts
        if elapsed < 120:
            return "Working"
    except (ValueError, IndexError):
        pass

    return "Idle"
