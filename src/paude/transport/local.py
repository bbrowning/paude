"""Local transport — runs commands via subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path

from paude.transport.file_copy import (
    copies_directory_contents,
    copy_path,
    without_contents_suffix,
)


class LocalTransport:
    """Execute commands locally via subprocess.run()."""

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
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=text,
            input=input,
            timeout=timeout,
        )

    def run_interactive(self, cmd: list[str]) -> int:
        result = subprocess.run(cmd)
        return result.returncode

    def popen_binary(self, cmd: list[str]) -> subprocess.Popen[bytes]:
        """Start a command with binary stdout/stderr pipes for streaming.

        Unlike :meth:`run`, this returns immediately so the caller can consume
        stdout incrementally (e.g. a multi-GB tar stream). The caller owns
        draining both pipes and awaiting the process.
        """
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def machine(self) -> str:
        """Return the local machine's CPU architecture."""
        import platform as plat

        return plat.machine()

    def copy_to_host(self, local_path: str, host_path: str) -> None:
        """Copy a local path to another local path."""
        _copy_path(local_path, host_path)

    def copy_from_host(self, host_path: str, local_path: str) -> None:
        """Copy a local path to another local path."""
        _copy_path(host_path, local_path)

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def host_label(self) -> str:
        return "local"


def _copy_path(source_path: str, destination_path: str) -> None:
    """Copy a path while retaining trailing ``/.`` semantics."""
    contents = copies_directory_contents(source_path)
    if contents:
        source_path = without_contents_suffix(source_path)
    copy_path(Path(source_path), Path(destination_path), contents=contents)
