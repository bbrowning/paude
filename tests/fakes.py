"""Shared test doubles for subprocess.Popen-based lifecycle tests."""

from __future__ import annotations

import io
import subprocess


class FakePopen:
    """Minimal stand-in for a ``subprocess.Popen[bytes]``.

    ``pending_waits`` is how many ``wait()`` calls raise ``TimeoutExpired``
    (simulating a still-running process) before the real return code is
    reported; ``kill()`` clears the count so a subsequent ``wait()`` succeeds.
    """

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        pending_waits: int = 0,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._returncode = returncode
        self._pending_waits = pending_waits
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if self._pending_waits > 0:
            self._pending_waits -= 1
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return self._returncode

    def poll(self) -> int | None:
        return None if self._pending_waits > 0 else self._returncode

    def kill(self) -> None:
        self.killed = True
        self._pending_waits = 0
