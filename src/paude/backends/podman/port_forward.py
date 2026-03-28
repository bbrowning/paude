"""Port-forward management for Podman/Docker sessions.

Uses a background TCP proxy process that tunnels connections through
``podman exec`` + ``socat``, so the container sees every connection
from 127.0.0.1 (matching ``oc port-forward`` behaviour on OpenShift).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

from paude.backends.port_forward_utils import (
    check_running_pid,
    pid_file,
    stop_port_forward,
)

if TYPE_CHECKING:
    from paude.container.engine import ContainerEngine


class PodmanPortForwardManager:
    """Manages port-forward background processes for Podman/Docker sessions."""

    def __init__(self, engine: ContainerEngine) -> None:
        self._engine = engine

    def start(
        self,
        session_name: str,
        container_name: str,
        ports: list[tuple[int, int]],
    ) -> None:
        """Start port-forwarding for a session (idempotent).

        If a port-forward is already running for this session, does nothing.

        Args:
            session_name: Paude session name.
            container_name: Container name to exec into.
            ports: List of (host_port, container_port) tuples.
        """
        if not ports:
            return

        if check_running_pid(session_name):
            return

        proxy_cmd = [
            sys.executable,
            "-m",
            "paude.backends.podman.port_forward_proxy",
            "--parent-pid",
            str(os.getpid()),
        ]
        for host_port, container_port in ports:
            exec_cmd = self._build_exec_cmd(container_name, container_port)
            proxy_cmd.extend(["--forward", str(host_port), shlex.join(exec_cmd)])

        proc = subprocess.Popen(  # noqa: S603
            proxy_cmd,
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

    def _build_exec_cmd(self, container_name: str, container_port: int) -> list[str]:
        """Build the full exec command for a single port tunnel.

        For local transport, returns::

            ["podman", "exec", "-i", container_name,
             "socat", "STDIO", "TCP:127.0.0.1:<port>"]

        For SSH transport, wraps the above in the SSH prefix.
        """
        cmd = [
            self._engine.binary,
            "exec",
            "-i",
            container_name,
            "socat",
            "STDIO",
            f"TCP:127.0.0.1:{container_port}",
        ]

        transport = self._engine.transport
        if transport.is_remote:
            from paude.transport.ssh import SshTransport

            assert isinstance(transport, SshTransport)  # noqa: S101
            return [*transport.ssh_base(), "--", shlex.join(cmd)]

        return cmd
