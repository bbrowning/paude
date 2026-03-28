"""Shared utilities for port-forward PID file management."""

from __future__ import annotations

import os
import signal
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def pid_dir() -> Path:
    """Return the directory for storing port-forward PID files."""
    d = Path.home() / ".local" / "share" / "paude" / "port-forwards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_file(session_name: str) -> Path:
    """Return the PID file path for a session's port-forward."""
    return pid_dir() / f"{session_name}.pid"


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def check_running_pid(session_name: str) -> bool:
    """Return True if a port-forward process is already running for this session.

    Cleans up stale PID files as a side effect.
    """
    pf = pid_file(session_name)
    if not pf.is_file():
        return False
    try:
        pid = int(pf.read_text().strip())
        if is_process_running(pid):
            return True
    except (ValueError, OSError):
        pass
    pf.unlink(missing_ok=True)
    return False


def stop_port_forward(session_name: str) -> None:
    """Stop a port-forward process by session name and clean up the PID file."""
    pf = pid_file(session_name)
    if not pf.is_file():
        return

    try:
        pid = int(pf.read_text().strip())
        if is_process_running(pid):
            os.kill(pid, signal.SIGTERM)
    except (ValueError, OSError):
        pass

    pf.unlink(missing_ok=True)
