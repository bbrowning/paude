"""Container engine abstraction for podman/docker CLI compatibility."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from typing import IO, TYPE_CHECKING

from paude.subprocess_utils import drain_pipe, raise_on_nonzero, reap

if TYPE_CHECKING:
    from paude.transport.base import Transport

# How long stream_run gives a fully-drained process to exit naturally before
# killing it -- long enough that a legitimately finishing process never gets
# clobbered, short enough that a caller who didn't drain to EOF isn't left
# hanging.
_REAP_GRACE_SECONDS = 5.0
MINIMUM_PODMAN_VERSION = (4, 0)


class UnsupportedEngineError(RuntimeError):
    """Raised when an engine cannot provide required networking semantics."""


def _parse_podman_version(output: str) -> tuple[int, ...]:
    """Parse the effective version from a Podman JSON version report."""
    report = json.loads(output)
    if not isinstance(report, dict):
        raise ValueError("expected a JSON object")
    server = report.get("Server")
    source = server if isinstance(server, dict) else report.get("Client")
    if not isinstance(source, dict):
        raise ValueError("missing Client and Server version objects")
    raw_version = source.get("Version")
    if not isinstance(raw_version, str):
        raise ValueError("missing Version string")
    match = re.fullmatch(
        r"v?(\d+(?:\.\d+)*)(?:[-+][0-9A-Za-z.-]+)?", raw_version
    )
    if match is None:
        raise ValueError(f"invalid Version value {raw_version!r}")
    return tuple(int(part) for part in match.group(1).split("."))


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
        self._version_checked = False
        self._version: tuple[int, ...] | None = None
        self._version_error: str | None = None

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

    def popen_with_remote_redirect(
        self, *args: str, remote_output_path: str
    ) -> subprocess.Popen[bytes]:
        """Start a command with its stdout redirected to a file on the remote host.

        Mirrors :meth:`run`'s "prepend the binary, delegate to the transport"
        shape, but for :meth:`SshTransport.popen_remote_redirect` — only valid
        when this engine's transport is SSH-backed, since the redirect happens
        on the remote host and nothing crosses back to the client. Returns the
        live process rather than blocking, so a caller doing long-running
        progress polling (see
        :meth:`~paude.backends.podman.volume_archive.VolumeArchiver.export_volume_to_remote_file`)
        can kill it immediately on interrupt instead of blocking until the
        remote command finishes on its own.
        """
        from paude.transport.ssh import SshTransport

        if not isinstance(self._transport, SshTransport):
            raise RuntimeError(
                "popen_with_remote_redirect requires an SSH-backed engine"
            )
        cmd = [self.binary, *args]
        return self._transport.popen_remote_redirect(cmd, remote_output_path)

    @contextmanager
    def stream_run(self, *args: str) -> Iterator[IO[bytes]]:
        """Run a command and yield its stdout as a binary stream.

        Unlike :meth:`run`, the output is not captured up front — the caller gets
        a readable stdout stream so a large payload (e.g. a ``tar`` archive) can
        be consumed incrementally without buffering it in memory or staging it on
        the engine host. Works transparently for local and SSH transports.

        This owns the process lifecycle so callers only handle bytes: stderr is
        drained concurrently (so it can't fill its pipe and deadlock the stdout
        read), the process is always reaped — killed immediately if the
        caller's block raises, or after a short grace period if it returns
        without draining stdout to EOF, so a stalled stdout pipe can't hang —
        and a non-zero exit is surfaced as a ``RuntimeError`` carrying the
        command's stderr.
        """
        proc = self._transport.popen_binary([self.binary, *args])
        stderr_chunks: list[bytes] = []
        stderr_thread = drain_pipe(proc.stderr, stderr_chunks)

        assert proc.stdout is not None  # noqa: S101 - popen_binary always pipes it
        try:
            yield proc.stdout
        except BaseException:
            reap(proc, stderr_thread)
            raise
        else:
            returncode = reap(proc, stderr_thread, grace=_REAP_GRACE_SECONDS)
        finally:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()

        raise_on_nonzero(returncode, stderr_chunks)

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

    @property
    def version(self) -> tuple[int, ...] | None:
        """Return the effective Podman version, probing at most once.

        Remote Podman clients report both client and server versions. The
        server controls networking behavior, so prefer it when present.
        Docker does not need this Podman-specific capability probe.
        """
        if not self.is_podman:
            return None
        if not self._version_checked:
            self._probe_version()
        return self._version

    def _probe_version(self) -> None:
        """Populate the cached Podman version and diagnostic."""
        self._version_checked = True
        try:
            result = self.run("version", "--format", "json", check=False)
        except Exception as exc:
            self._version_error = f"command failed: {exc}"
            return
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "no output").strip()
            self._version_error = f"exit {result.returncode}: {detail}"
            return
        try:
            self._version = _parse_podman_version(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            self._version_error = f"invalid JSON output {result.stdout!r}: {exc}"

    def ensure_supported_networking(self) -> None:
        """Require Podman networking semantics needed for proxy isolation."""
        if not self.is_podman:
            return
        version = self.version
        command = f"{self.binary} version --format json"
        if version is None:
            raise UnsupportedEngineError(
                f"Could not determine the Podman version using `{command}` "
                f"({self._version_error or 'unknown error'}). Paude requires "
                "Podman 4.0+ for isolated proxy networking."
            )
        required = MINIMUM_PODMAN_VERSION
        comparable = version + (0,) * max(0, len(required) - len(version))
        if comparable < required:
            detected = ".".join(str(part) for part in version)
            raise UnsupportedEngineError(
                f"Podman {detected} is unsupported; Paude requires Podman "
                "4.0+ for repeated --network flags and per-network static "
                "IPs used by isolated proxy networking."
            )

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

        Podman 4.0+ embeds the IP in the ``--network`` value (``net:ip=...``);
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

        Podman 4.0+ supports this; Docker requires ``docker network connect``
        after container creation.
        """
        return self.binary != "docker"

    @property
    def default_bridge_network(self) -> str:
        """Name of the default bridge network.

        Podman uses "podman"; Docker uses "bridge".
        """
        return "podman" if self.binary == "podman" else "bridge"
