"""SSH transport — runs commands on a remote host via SSH."""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from paude.platform import is_macos
from paude.transport.file_copy import (
    copies_directory_contents,
    copy_path,
    without_contents_suffix,
)

SSH_CONNECT_TIMEOUT = 10
SSH_STATUS_TIMEOUT = 2


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
        connect_timeout: int = SSH_CONNECT_TIMEOUT,
    ) -> None:
        self._host = host
        self._key = key
        self._port = port
        self._connect_timeout = connect_timeout

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
            "-o",
            f"ConnectTimeout={self._connect_timeout}",
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

    def popen_binary(self, cmd: list[str]) -> subprocess.Popen[bytes]:
        """Start a remote command with binary stdout/stderr pipes for streaming.

        The command runs over SSH (no TTY, binary-safe) so its stdout can be
        consumed incrementally on the client — e.g. a multi-GB tar stream that
        must never land on the remote host's disk. Mirrors the tar-pipe shape
        used by :meth:`copy_from_host`. The caller owns draining both pipes and
        awaiting the process.
        """
        full = [*self.ssh_base(), "--", shlex.join(cmd)]
        return subprocess.Popen(
            full,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_with_remote_redirect(
        self, cmd: list[str], remote_output_path: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run ``cmd`` on the remote host with its stdout redirected to a file.

        Unlike :meth:`run`, whose argv is shell-escaped as literal arguments,
        this appends a real ``>`` redirect that the remote shell evaluates —
        so a large command's stdout (e.g. a tar stream) is written straight to
        remote disk and never crosses the SSH channel back to the client. This
        is the same "let the remote shell do it" trick :meth:`_pipe_tar` uses,
        just redirecting to a static path instead of piping into a second
        process. Built as an ``sh -c`` wrapper around :meth:`run` (rather than
        a second, hand-rolled ``subprocess.run`` call) so it shares one place
        for the actual SSH invocation.
        """
        inner = f"{shlex.join(cmd)} > {shlex.quote(remote_output_path)}"
        result = self.run(["sh", "-c", inner], check=False)
        _raise_on_failure(
            check and result.returncode != 0,
            result.stderr,
            f"command failed (exit {result.returncode})",
        )
        return result

    def file_size(self, remote_path: str) -> int | None:
        """Best-effort size in bytes of a file on the remote host, or None.

        Uses ``wc -c`` (not ``stat -c%s``/``stat -f%z``) so it works unmodified
        whether the remote host is Linux or macOS.
        """
        result = self.run(
            ["sh", "-c", f"wc -c < {shlex.quote(remote_path)} 2>/dev/null || echo 0"],
            check=False,
        )
        text = result.stdout.strip()
        return int(text) if text.isdigit() else None

    def free_bytes(self, remote_path: str) -> int | None:
        """Best-effort free space in bytes for a directory on the remote host.

        Uses ``df -Pk`` (POSIX-portable output) rather than GNU-only flags like
        ``--output``, since the remote host isn't necessarily Linux.
        """
        result = self.run(["df", "-Pk", remote_path], check=False)
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return None
        fields = lines[-1].split()
        if len(fields) < 4 or not fields[3].isdigit():
            return None
        return int(fields[3]) * 1024

    def copy_to_host(self, local_path: str, host_path: str) -> None:
        """Copy a local path to a path on the SSH host."""
        contents = copies_directory_contents(local_path)
        source_path = without_contents_suffix(local_path) if contents else local_path
        source = Path(source_path)
        if not source.exists() and not source.is_symlink():
            raise FileNotFoundError(local_path)
        if contents and (source.is_symlink() or not source.is_dir()):
            raise ValueError(f"Copy source is not a directory: {local_path}")

        if contents:
            remote_directory = (
                without_contents_suffix(host_path)
                if copies_directory_contents(host_path)
                else host_path
            )
            self.run(["mkdir", "-p", remote_directory])
            tar_cmd = _local_tar_command(source, contents=True)
            remote_cmd = f"tar -xf - -C {shlex.quote(remote_directory)}"
            self._pipe_tar(tar_cmd, remote_cmd)
            return

        remote_parent = str(PurePosixPath(host_path).parent)
        self.run(["mkdir", "-p", remote_parent])
        tar_source = source if source.name else source.resolve()
        tar_name = tar_source.name
        tar_cmd = _local_tar_command(tar_source)
        remote_cmd = _remote_extract_command(remote_parent, tar_name, host_path)
        self._pipe_tar(tar_cmd, remote_cmd)

    def copy_from_host(self, host_path: str, local_path: str) -> None:
        """Copy a path from the SSH host to a local path."""
        contents = copies_directory_contents(host_path)
        if contents:
            remote_source = without_contents_suffix(host_path)
            remote_cmd = f"tar -cf - -C {shlex.quote(remote_source)} ."
            source_name = None
        else:
            trimmed_host_path = host_path.rstrip("/")
            source_name = PurePosixPath(trimmed_host_path).name
            if not source_name:
                raise ValueError(f"Invalid remote copy path: {host_path}")
            remote_parent = str(PurePosixPath(trimmed_host_path).parent)
            archive_name = f"./{source_name}"
            remote_cmd = (
                f"tar -cf - -C {shlex.quote(remote_parent)} {shlex.quote(archive_name)}"
            )
        remote_process = subprocess.Popen(
            [*self.ssh_base(), "--", remote_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        destination = Path(local_path)
        with tempfile.TemporaryDirectory(prefix="paude-copy-") as temp_dir:
            temp_root = Path(temp_dir)
            extract_process = subprocess.Popen(
                ["tar", "-xf", "-", "-C", str(temp_root)],
                stdin=remote_process.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if remote_process.stdout is not None:
                remote_process.stdout.close()
            _extract_stdout, extract_stderr = extract_process.communicate()
            _remote_stdout, remote_stderr = remote_process.communicate()
            if remote_process.returncode != 0:
                detail = remote_stderr.decode(errors="replace").strip()
                message = detail or f"SSH file transfer failed for '{host_path}'"
                raise RuntimeError(message)
            if extract_process.returncode != 0:
                detail = extract_stderr.decode(errors="replace").strip()
                message = detail or f"Local archive extraction failed for '{host_path}'"
                raise RuntimeError(message)
            if contents:
                copy_path(temp_root, destination, contents=True)
            else:
                if source_name is None:
                    raise RuntimeError("Remote copy source name was not resolved")
                source = temp_root / source_name
                if not source.exists() and not source.is_symlink():
                    raise RuntimeError(f"Remote copy did not contain '{source_name}'")
                copy_path(source, destination)

    def _pipe_tar(self, tar_cmd: list[str], remote_cmd: str) -> None:
        tar_process = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
        remote_process = subprocess.Popen(
            [*self.ssh_base(), "--", remote_cmd],
            stdin=tar_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if tar_process.stdout is not None:
            tar_process.stdout.close()
        _stdout, stderr = remote_process.communicate()
        tar_returncode = tar_process.wait()
        _raise_on_failure(
            tar_returncode != 0 or remote_process.returncode != 0,
            stderr.decode(errors="replace") if stderr else "",
            "SSH file transfer failed",
        )

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

    def machine(self) -> str:
        """Query the remote host's CPU architecture via ``uname -m``."""
        try:
            result = self.run(["uname", "-m"], check=False)
        except (subprocess.SubprocessError, OSError) as e:
            raise RuntimeError(
                f"Could not determine architecture of {self._host}: {e}"
            ) from e
        if result.returncode != 0:
            raise RuntimeError(f"Could not determine architecture of {self._host}")
        return result.stdout.strip()


def _raise_on_failure(failed: bool, stderr: str, fallback: str) -> None:
    """Raise ``RuntimeError(stderr or fallback)`` if ``failed``."""
    if failed:
        raise RuntimeError(stderr.strip() or fallback)


def _remote_extract_command(
    remote_parent: str, source_name: str, host_path: str
) -> str:
    """Build a remote extraction command that honors the destination name."""
    extract = f"tar -xf - -C {shlex.quote(remote_parent)}"
    if source_name == PurePosixPath(host_path).name:
        return extract

    temp_template = str(PurePosixPath(remote_parent) / ".paude-copy-XXXXXX")
    temp_source = f'"$temp_dir"/{shlex.quote(source_name)}'
    return (
        f"temp_dir=$(mktemp -d {shlex.quote(temp_template)}) || exit; "
        "trap 'rm -rf -- \"$temp_dir\"' EXIT; "
        'tar -xf - -C "$temp_dir" && '
        f"mv -f -- {temp_source} {shlex.quote(host_path)}"
    )


def _local_tar_command(source: Path, *, contents: bool = False) -> list[str]:
    """Build a local tar command using a portable archive operand."""
    command = ["tar"]
    if is_macos():
        command.append("--no-mac-metadata")
    if contents:
        command.extend(["-cf", "-", "-C", str(source), "."])
    else:
        command.extend(["-cf", "-", "-C", str(source.parent), f"./{source.name}"])
    return command


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
