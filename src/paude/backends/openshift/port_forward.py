"""Port-forward management for OpenShift sessions."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from paude.backends.port_forward_utils import (
    check_running_pid,
    log_file,
    pid_file,
    stop_port_forward,
)


class PortForwardResult(NamedTuple):
    """Result of starting a port-forward process."""

    proc: subprocess.Popen[bytes]
    cmd: list[str]
    log_path: Path


def launch_port_forward(
    cmd: list[str], log_path: Path, session_name: str
) -> subprocess.Popen[bytes]:
    """Launch an oc port-forward subprocess.

    Opens the log file in append mode, starts the process, and writes
    the PID file.  The caller is responsible for monitoring the process.
    """
    log_fh = open(log_path, "a")  # noqa: SIM115, PTH123
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        log_fh.close()

    pid_file(session_name).write_text(str(proc.pid))
    return proc


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
    ) -> PortForwardResult | None:
        """Start port-forwarding for a session (idempotent).

        If a port-forward is already running for this session, does nothing.

        Args:
            session_name: Paude session name.
            pod_name: Kubernetes pod name to forward to.
            ports: List of (host_port, container_port) tuples.

        Returns:
            A PortForwardResult with the process, command, and log path,
            or None if no ports or already running.
        """
        if not ports:
            return None

        if check_running_pid(session_name):
            return None

        cmd = ["oc"]
        if self._context:
            cmd.extend(["--context", self._context])
        cmd.extend(["port-forward", "-n", self._namespace, pod_name])
        for host_port, container_port in ports:
            cmd.append(f"{host_port}:{container_port}")

        lp = log_file(session_name)
        proc = launch_port_forward(cmd, lp, session_name)

        for host_port, _container_port in ports:
            print(
                f"Port-forward active: http://localhost:{host_port}",
                file=sys.stderr,
            )

        return PortForwardResult(proc=proc, cmd=cmd, log_path=lp)

    def stop(self, session_name: str) -> None:
        """Stop port-forwarding for a session."""
        stop_port_forward(session_name)
