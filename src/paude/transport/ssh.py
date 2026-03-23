"""SSH transport — runs commands on a remote host via SSH."""

from __future__ import annotations

import shlex
import subprocess


class SshTransport:
    """Execute commands on a remote host via SSH.

    All container engine commands are prefixed with ``ssh host --``
    so they execute on the remote machine transparently.
    """

    def __init__(
        self,
        host: str,
        key: str | None = None,
        port: int | None = None,
    ) -> None:
        self._host = host
        self._key = key
        self._port = port

    @property
    def host(self) -> str:
        return self._host

    @property
    def key(self) -> str | None:
        return self._key

    @property
    def port(self) -> int | None:
        return self._port

    def ssh_base(self) -> list[str]:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self._key:
            cmd.extend(["-i", self._key])
        if self._port:
            cmd.extend(["-p", str(self._port)])
        cmd.append(self._host)
        return cmd

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        capture: bool = True,
        text: bool = True,
        input: str | None = None,  # noqa: A002
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        full = [*self.ssh_base(), "--", shlex.join(cmd)]
        return subprocess.run(
            full,
            check=check,
            capture_output=capture,
            text=text,
            input=input,
            timeout=timeout,
        )

    def run_interactive(self, cmd: list[str]) -> int:
        full = [*self.ssh_base(), "-t", "--", shlex.join(cmd)]
        result = subprocess.run(full)
        return result.returncode

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def host_label(self) -> str:
        return self._host

    def validate(self) -> None:
        """Test SSH connectivity. Raises RuntimeError on failure."""
        result = subprocess.run(
            [*self.ssh_base(), "true"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH connection to {self._host} failed")

    def validate_engine(self, engine_binary: str) -> None:
        """Verify engine binary exists on remote."""
        result = self.run([engine_binary, "--version"], check=False)
        if result.returncode != 0:
            raise RuntimeError(f"'{engine_binary}' not found on {self._host}")


def parse_ssh_host(host_str: str) -> tuple[str, int | None]:
    """Parse 'user@hostname[:port]' -> (user@hostname, port).

    Supports formats:
        hostname          -> (hostname, None)
        user@hostname     -> (user@hostname, None)
        hostname:22       -> (hostname, 22)
        user@hostname:22  -> (user@hostname, 22)
    """
    if ":" in host_str:
        host_part, port_str = host_str.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            # Not a valid port, treat entire string as host
            return (host_str, None)
        return (host_part, port)
    return (host_str, None)
