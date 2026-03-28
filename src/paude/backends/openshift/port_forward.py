"""Port-forward management for OpenShift sessions."""

from __future__ import annotations

import subprocess
import sys

from paude.backends.port_forward_utils import (
    check_running_pid,
    pid_file,
    stop_port_forward,
)


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

        if check_running_pid(session_name):
            return

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

        pid_file(session_name).write_text(str(proc.pid))

        for host_port, _container_port in ports:
            print(
                f"Port-forward active: http://localhost:{host_port}",
                file=sys.stderr,
            )

    def stop(self, session_name: str) -> None:
        """Stop port-forwarding for a session."""
        stop_port_forward(session_name)
