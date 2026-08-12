"""Container engine abstraction for podman/docker CLI compatibility."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, TYPE_CHECKING

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

    def run_with_remote_redirect(
        self, *args: str, remote_output_path: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a command with its stdout redirected to a file on the remote host.

        Mirrors :meth:`run`'s "prepend the binary, delegate to the transport"
        shape, but for :meth:`SshTransport.run_with_remote_redirect` — only
        valid when this engine's transport is SSH-backed, since the redirect
        happens on the remote host and nothing crosses back to the client.
        """
        from paude.transport.ssh import SshTransport

        if not isinstance(self._transport, SshTransport):
            raise RuntimeError("run_with_remote_redirect requires an SSH-backed engine")
        cmd = [self.binary, *args]
        return self._transport.run_with_remote_redirect(
            cmd, remote_output_path, check=check
        )

    @contextmanager
    def stream_run(self, *args: str) -> Iterator[IO[bytes]]:
        """Run a command and yield its stdout as a binary stream.

        Unlike :meth:`run`, the output is not captured up front — the caller gets
        a readable stdout stream so a large payload (e.g. a ``tar`` archive) can
        be consumed incrementally without buffering it in memory or staging it on
        the engine host. Works transparently for local and SSH transports.

        This owns the process lifecycle so callers only handle bytes: stderr is
        drained concurrently (so it can't fill its pipe and deadlock the stdout
        read), the process is always reaped — killed if the caller's block raises,
        so a stalled stdout pipe can't hang — and a non-zero exit is surfaced as a
        ``RuntimeError`` carrying the command's stderr.
        """
        proc = self._transport.popen_binary([self.binary, *args])
        stderr_chunks: list[bytes] = []

        def _drain_stderr() -> None:
            if proc.stderr is not None:  # pragma: no cover - always piped
                stderr_chunks.append(proc.stderr.read())

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        assert proc.stdout is not None  # noqa: S101 - popen_binary always pipes it
        try:
            yield proc.stdout
            returncode = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            stderr_thread.join()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()

        if returncode != 0:
            detail = b"".join(stderr_chunks).decode(errors="replace").strip()
            raise RuntimeError(detail or f"command failed (exit {returncode})")

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

    def _exists(self, resource_type: str, name: str) -> bool:
        """Check if a resource exists.

        Podman has ``<type> exists``; Docker requires ``<type> inspect``.
        """
        subcmd = "exists" if self.binary == "podman" else "inspect"
        result = self.run(resource_type, subcmd, name, check=False)
        return result.returncode == 0

    def image_exists(self, tag: str) -> bool:
        """Check if a container image exists locally."""
        return self._exists("image", tag)

    def network_exists(self, name: str) -> bool:
        """Check if a container network exists."""
        return self._exists("network", name)

    def volume_exists(self, name: str) -> bool:
        """Check if a volume exists."""
        return self._exists("volume", name)

    def container_exists(self, name: str) -> bool:
        """Check if a container exists."""
        return self._exists("container", name)

    def gpu_args(self, gpu_value: str) -> list[str]:
        """Build GPU passthrough arguments.

        Docker uses ``--gpus``; Podman uses CDI device syntax.
        """
        if self.binary == "docker":
            return ["--gpus", gpu_value]
        return ["--device", f"nvidia.com/gpu={gpu_value}"]

    def network_args(self, network: str, network_ip: str | None = None) -> list[str]:
        """Build ``--network`` arguments, embedding a static IP if given.

        Podman embeds the IP in the ``--network`` value (``net:ip=...``);
        Docker requires the IP as a separate ``--ip`` flag.
        """
        if network_ip and not self.is_podman:
            return ["--network", network, "--ip", network_ip]
        net_spec = f"{network}:ip={network_ip}" if network_ip else network
        return ["--network", net_spec]

    @property
    def image_name_format(self) -> str:
        """Go template for extracting image name from container inspect.

        Docker uses ``.Config.Image``; Podman uses ``.ImageName``.
        """
        return "{{.ImageName}}" if self.binary == "podman" else "{{.Config.Image}}"

    @property
    def is_podman(self) -> bool:
        """Whether the engine is Podman (not Docker)."""
        return self.binary != "docker"

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
