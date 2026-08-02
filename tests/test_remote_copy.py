"""Tests for copying through a remote container engine host."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paude.backends.podman.file_copy import (
    copy_from_container,
    copy_to_container,
)


def _remote_engine() -> MagicMock:
    engine = MagicMock()
    engine.is_remote = True
    engine.transport.run.return_value = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="/tmp/paude-copy-test\n", stderr=""
    )
    return engine


def test_remote_copy_to_stages_on_engine_host() -> None:
    engine = _remote_engine()

    copy_to_container(engine, "paude-session", "./input.txt", "/tmp/input.txt")

    engine.transport.copy_to_host.assert_called_once_with(
        "./input.txt", "/tmp/paude-copy-test/input.txt"
    )
    engine.run.assert_called_once_with(
        "cp",
        "/tmp/paude-copy-test/input.txt",
        "paude-session:/tmp/input.txt",
    )
    engine.transport.run.assert_any_call(
        ["rm", "-rf", "/tmp/paude-copy-test"], check=False
    )


def test_remote_copy_from_stages_on_engine_host() -> None:
    engine = _remote_engine()

    copy_from_container(
        engine, "paude-session", "/pvc/workspace/output.log", "./output.log"
    )

    engine.run.assert_called_once_with(
        "cp",
        "paude-session:/pvc/workspace/output.log",
        "/tmp/paude-copy-test",
    )
    engine.transport.copy_from_host.assert_called_once_with(
        "/tmp/paude-copy-test/output.log", "./output.log"
    )
    engine.transport.run.assert_any_call(
        ["rm", "-rf", "/tmp/paude-copy-test"], check=False
    )


def test_remote_copy_cleans_up_when_transfer_fails() -> None:
    engine = _remote_engine()
    engine.transport.copy_to_host.side_effect = OSError("SSH failed")

    with pytest.raises(OSError, match="SSH failed"):
        copy_to_container(engine, "paude-session", "input.txt", "/tmp/input.txt")

    engine.run.assert_not_called()
    engine.transport.run.assert_any_call(
        ["rm", "-rf", "/tmp/paude-copy-test"], check=False
    )


def test_local_copy_does_not_stage() -> None:
    engine = MagicMock()
    engine.is_remote = False

    copy_to_container(engine, "paude-session", "input.txt", "/tmp/input.txt")

    engine.run.assert_called_once_with(
        "cp", "input.txt", "paude-session:/tmp/input.txt"
    )
    engine.transport.copy_to_host.assert_not_called()


def test_copy_path_dot_uses_resolved_directory_name() -> None:
    engine = _remote_engine()

    copy_to_container(engine, "paude-session", ".", "/tmp/workspace")

    staged_path = engine.transport.copy_to_host.call_args.args[1]
    assert staged_path.endswith(f"/{Path.cwd().name}")


def test_remote_copy_to_preserves_directory_contents_semantics() -> None:
    engine = _remote_engine()

    copy_to_container(engine, "paude-session", "./input/.", "/tmp/input")

    engine.transport.copy_to_host.assert_called_once_with(
        "./input/.", "/tmp/paude-copy-test/."
    )
    engine.run.assert_called_once_with(
        "cp", "/tmp/paude-copy-test/.", "paude-session:/tmp/input"
    )


def test_remote_copy_from_preserves_directory_contents_semantics() -> None:
    engine = _remote_engine()

    copy_from_container(
        engine, "paude-session", "/pvc/workspace/results/.", "./results"
    )

    engine.run.assert_called_once_with(
        "cp",
        "paude-session:/pvc/workspace/results/.",
        "/tmp/paude-copy-test",
    )
    engine.transport.copy_from_host.assert_called_once_with(
        "/tmp/paude-copy-test/.", "./results"
    )
