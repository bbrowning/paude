"""Local transport — runs commands via subprocess."""

from __future__ import annotations

import subprocess


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

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def host_label(self) -> str:
        return "local"
