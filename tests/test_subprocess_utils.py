"""Tests for shared subprocess-lifecycle helpers."""

from __future__ import annotations

import io
import threading

import pytest

from paude.subprocess_utils import drain_pipe, raise_on_nonzero, reap
from tests.fakes import FakePopen


class TestReap:
    def test_already_exited_no_grace(self) -> None:
        proc = FakePopen(returncode=0)
        assert reap(proc) == 0
        assert proc.killed is False

    def test_still_running_no_grace_gets_killed(self) -> None:
        proc = FakePopen(pending_waits=1, returncode=-9)
        assert reap(proc) == -9
        assert proc.killed is True

    def test_exits_within_grace_not_killed(self) -> None:
        proc = FakePopen(returncode=0)
        assert reap(proc, grace=5.0) == 0
        assert proc.killed is False

    def test_exceeds_grace_gets_killed(self) -> None:
        proc = FakePopen(pending_waits=1, returncode=-9)
        assert reap(proc, grace=5.0) == -9
        assert proc.killed is True

    def test_joins_drain_threads(self) -> None:
        proc = FakePopen(returncode=0)
        joined = threading.Event()
        thread = threading.Thread(target=joined.set)
        thread.start()
        reap(proc, thread)
        assert joined.is_set()


class TestDrainPipe:
    def test_drains_full_pipe_contents(self) -> None:
        pipe = io.BytesIO(b"hello world")
        chunks: list[bytes] = []
        thread = drain_pipe(pipe, chunks)
        thread.join()
        assert chunks == [b"hello world"]

    def test_none_pipe_is_a_noop(self) -> None:
        chunks: list[bytes] = []
        thread = drain_pipe(None, chunks)
        thread.join()
        assert chunks == []


class TestRaiseOnNonzero:
    def test_zero_returncode_does_not_raise(self) -> None:
        raise_on_nonzero(0, [b"ignored"])

    def test_nonzero_returncode_raises_with_stderr(self) -> None:
        with pytest.raises(RuntimeError, match="boom"):
            raise_on_nonzero(1, [b"bo", b"om\n"])

    def test_nonzero_returncode_without_stderr_falls_back_to_exit_code(self) -> None:
        with pytest.raises(RuntimeError, match="exit 2"):
            raise_on_nonzero(2, [])
