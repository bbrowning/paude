"""Transport protocol for executing commands locally or remotely."""

from __future__ import annotations

import subprocess
from typing import Protocol


class Transport(Protocol):
    """Protocol for command execution transport.

    Implementations handle how commands are executed — locally via
    subprocess, or remotely via SSH.
    """

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        capture: bool = True,
        text: bool = True,
        input: str | None = None,  # noqa: A002
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]: ...

    def run_interactive(self, cmd: list[str]) -> int: ...

    def copy_to_host(self, local_path: str, host_path: str) -> None: ...

    def copy_from_host(self, host_path: str, local_path: str) -> None: ...

    @property
    def is_remote(self) -> bool: ...

    @property
    def host_label(self) -> str: ...
