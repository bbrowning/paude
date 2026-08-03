"""Tests for persistent file operations inside containers."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from paude.container.files import ContainerFileManager


def test_read_file_returns_none_when_path_is_missing() -> None:
    engine = MagicMock()
    engine.run.return_value = MagicMock(returncode=1)

    assert ContainerFileManager(engine).read_file("container", "/missing") is None


def test_read_file_returns_exact_content() -> None:
    engine = MagicMock()
    engine.run.side_effect = [
        MagicMock(returncode=0),
        MagicMock(returncode=0, stdout="# config\nvalue = true\n"),
    ]

    content = ContainerFileManager(engine).read_file("container", "/config.toml")

    assert content == "# config\nvalue = true\n"


def test_read_file_raises_when_existing_path_cannot_be_read() -> None:
    engine = MagicMock(binary="podman")
    engine.run.side_effect = [
        MagicMock(returncode=0),
        MagicMock(returncode=1, stdout="", stderr="permission denied"),
    ]

    with pytest.raises(subprocess.CalledProcessError):
        ContainerFileManager(engine).read_file("container", "/config.toml")


def test_replace_file_uses_same_directory_atomic_rename() -> None:
    engine = MagicMock()

    ContainerFileManager(engine).replace_file(
        "container",
        "/pvc/.codex/config.toml",
        'model = "gpt"\n',
        owner="paude",
    )

    call = engine.run.call_args
    command = call.args
    script = command[command.index("-c") + 1]
    assert 'mktemp "${target}.tmp.XXXXXX"' in script
    assert 'mv -f "$temporary" "$target"' in script
    assert command[-3:] == ("/pvc/.codex/config.toml", "paude", "600")
    assert call.kwargs["input"] == 'model = "gpt"\n'
