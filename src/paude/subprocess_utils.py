"""Shared helpers for managing subprocess lifecycles across transports."""

from __future__ import annotations

import subprocess
import threading
from typing import IO


def drain_pipe(pipe: IO[bytes] | None, chunks: list[bytes]) -> threading.Thread:
    """Start a daemon thread that reads ``pipe`` fully into ``chunks``.

    Needed whenever a caller holds one of a process's pipes open indefinitely
    (e.g. streaming stdout) -- without this, the other pipe (stderr) fills up
    and deadlocks the child.
    """

    def _drain() -> None:
        if pipe is not None:
            chunks.append(pipe.read())

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    return thread


def reap(
    proc: subprocess.Popen[bytes], *threads: threading.Thread, grace: float = 0.0
) -> int:
    """Wait for ``proc`` to exit, killing it if it's still running.

    With ``grace > 0``, first waits up to ``grace`` seconds for a natural exit
    before killing -- for the common case where the process is already
    finishing on its own and forcing a signal would only clobber a legitimate
    exit code. With the default ``grace=0``, ``wait(timeout=0)`` reports
    immediately whether it's still running, so this kills right away:
    appropriate when the caller has already decided to abandon the process
    (e.g. an exception or interrupt), so there's nothing to lose by not
    waiting.
    """
    try:
        returncode = proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        returncode = proc.wait()
    for thread in threads:
        thread.join()
    return returncode


def raise_on_nonzero(returncode: int, stderr_chunks: list[bytes]) -> None:
    """Raise ``RuntimeError`` carrying drained stderr if ``returncode`` is nonzero."""
    if returncode != 0:
        detail = b"".join(stderr_chunks).decode(errors="replace").strip()
        raise RuntimeError(detail or f"command failed (exit {returncode})")
