"""Tests for the durable JSON store helpers (paude.json_store)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paude.json_store import atomic_write_json, read_json


def test_read_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"a": 1})
    assert read_json(path) == {"a": 1}


def test_read_json_missing_returns_empty(tmp_path: Path) -> None:
    assert read_json(tmp_path / "nope.json") == {}


def test_read_json_corrupt_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text("{ not valid json")
    assert read_json(path) == {}


def test_read_json_non_object_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    path.write_text(json.dumps([1, 2, 3]))
    assert read_json(path) == {}


def test_read_json_directory_returns_empty(tmp_path: Path) -> None:
    """A path replaced by a directory (IsADirectoryError) degrades to {}."""
    path = tmp_path / "adir"
    path.mkdir()
    assert read_json(path) == {}


def test_read_json_unreadable_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable file (PermissionError) degrades to {} rather than raising."""
    path = tmp_path / "data.json"
    path.write_text("{}")

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "read_text", _boom)
    assert read_json(path) == {}
