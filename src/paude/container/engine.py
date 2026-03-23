"""Container engine abstraction for podman/docker CLI compatibility."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.transport.base import Transport


class ContainerEngine:
    """Abstraction over container CLI (podman or docker).

    Wraps subprocess calls with the configured binary name and provides
    compatibility shims for commands that differ between engines.

    When a ``Transport`` is provided, all commands are routed through it,
    enabling transparent remote execution over SSH.
    """

    def __init__(
        self,
        engine: str = "podman",
        transport: Transport | None = None,
    ) -> None:
        self.binary = engine
        if transport is None:
            from paude.transport.local import LocalTransport

            transport = LocalTransport()
        self._transport = transport

    def run(
        self,
        *args: str,
        check: bool = True,
        capture: bool = True,
        input: str | None = None,  # noqa: A002
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a container engine command.

        Args:
            *args: Arguments to pass to the engine binary.
            check: Raise on non-zero exit code.
            capture: Capture stdout/stderr.
            input: String to send to stdin.
            timeout: Timeout in seconds.

        Returns:
            CompletedProcess result.
        """
        cmd = [self.binary, *args]
        return self._transport.run(
            cmd,
            check=check,
            capture=capture,
            input=input,
            timeout=timeout,
        )

    def run_interactive(self, *args: str) -> int:
        """Run an interactive container engine command (with TTY).

        Returns:
            Exit code from the command.
        """
        cmd = [self.binary, *args]
        return self._transport.run_interactive(cmd)

    @property
    def is_remote(self) -> bool:
        """Whether commands are executed on a remote host."""
        return self._transport.is_remote

    @property
    def host_label(self) -> str:
        """Human-readable label for the execution host."""
        return self._transport.host_label

    @property
    def transport(self) -> Transport:
        """Access the underlying transport."""
        return self._transport

    def image_exists(self, tag: str) -> bool:
        """Check if a container image exists locally.

        Podman has ``image exists``; Docker requires ``image inspect``.
        """
        if self.binary == "podman":
            result = self.run("image", "exists", tag, check=False)
        else:
            result = self.run("image", "inspect", tag, check=False)
        return result.returncode == 0

    def network_exists(self, name: str) -> bool:
        """Check if a container network exists.

        Podman has ``network exists``; Docker requires ``network inspect``.
        """
        if self.binary == "podman":
            result = self.run("network", "exists", name, check=False)
        else:
            result = self.run("network", "inspect", name, check=False)
        return result.returncode == 0

    def volume_exists(self, name: str) -> bool:
        """Check if a volume exists.

        Podman has ``volume exists``; Docker requires ``volume inspect``.
        """
        if self.binary == "podman":
            result = self.run("volume", "exists", name, check=False)
        else:
            result = self.run("volume", "inspect", name, check=False)
        return result.returncode == 0

    def container_exists(self, name: str) -> bool:
        """Check if a container exists.

        Podman has ``container exists``; Docker requires ``container inspect``.
        """
        if self.binary == "podman":
            result = self.run("container", "exists", name, check=False)
        else:
            result = self.run("container", "inspect", name, check=False)
        return result.returncode == 0

    @property
    def supports_secrets(self) -> bool:
        """Whether the engine supports standalone secrets.

        Docker secrets are Swarm-only; Podman supports rootless secrets.
        """
        return self.binary != "docker"

    @property
    def supports_multi_network_create(self) -> bool:
        """Whether --network net1,net2 works in create/run.

        Podman supports this; Docker requires ``docker network connect``
        after container creation.
        """
        return self.binary != "docker"

    @property
    def default_bridge_network(self) -> str:
        """Name of the default bridge network.

        Podman uses "podman"; Docker uses "bridge".
        """
        return "podman" if self.binary == "podman" else "bridge"
