"""Port-forward management for OpenShift sessions."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path


def _pid_dir() -> Path:
    """Return the directory for storing port-forward PID files."""
    d = Path.home() / ".local" / "share" / "paude" / "port-forwards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_file(session_name: str) -> Path:
    """Return the PID file path for a session's port-forward."""
    return _pid_dir() / f"{session_name}.pid"


def _is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class PortForwardManager:
    """Manages oc port-forward background processes for OpenShift sessions."""

    def __init__(self, namespace: str, context: str | None = None) -> None:
        self._namespace = namespace
        self._context = context

    def start(
        self,
        session_name: str,
        pod_name: str,
        ports: list[tuple[int, int]],
    ) -> None:
        """Start port-forwarding for a session (idempotent).

        If a port-forward is already running for this session, does nothing.

        Args:
            session_name: Paude session name.
            pod_name: Kubernetes pod name to forward to.
            ports: List of (host_port, container_port) tuples.
        """
        if not ports:
            return

        # Check if already running
        pf = _pid_file(session_name)
        if pf.is_file():
            try:
                pid = int(pf.read_text().strip())
                if _is_process_running(pid):
                    return  # Already running
            except (ValueError, OSError):
                pass
            pf.unlink(missing_ok=True)

        # Build oc port-forward command
        cmd = ["oc"]
        if self._context:
            cmd.extend(["--context", self._context])
        cmd.extend(["port-forward", "-n", self._namespace, pod_name])
        for host_port, container_port in ports:
            cmd.append(f"{host_port}:{container_port}")

        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Store PID
        pf.write_text(str(proc.pid))

        for host_port, _container_port in ports:
            print(
                f"Port-forward active: http://localhost:{host_port}",
                file=sys.stderr,
            )

    def stop(self, session_name: str) -> None:
        """Stop port-forwarding for a session.

        Args:
            session_name: Paude session name.
        """
        pf = _pid_file(session_name)
        if not pf.is_file():
            return

        try:
            pid = int(pf.read_text().strip())
            if _is_process_running(pid):
                os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            pass

        pf.unlink(missing_ok=True)
