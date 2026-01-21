"""Tests for dry-run output."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from paude.dry_run import show_dry_run


class TestShowDryRun:
    """Tests for show_dry_run."""

    def test_shows_workspace_path(self, capsys: pytest.CaptureFixture[str]):
        """Shows workspace path."""
        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test/workspace")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run({})

        captured = capsys.readouterr()
        assert "/test/workspace" in captured.out

    def test_shows_none_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """Shows 'none' when no config."""
        monkeypatch.chdir(tmp_path)
        show_dry_run({})

        captured = capsys.readouterr()
        assert "Configuration: none" in captured.out

    def test_shows_config_file_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """Shows config file when present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "paude.json"
        config.write_text(json.dumps({"base": "python:3.11"}))

        show_dry_run({})

        captured = capsys.readouterr()
        assert "paude.json" in captured.out

    def test_shows_packages_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """Shows packages when present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "paude.json"
        config.write_text(json.dumps({"base": "python:3.11", "packages": ["git", "vim"]}))

        show_dry_run({})

        captured = capsys.readouterr()
        assert "git" in captured.out
        assert "vim" in captured.out

    def test_shows_generated_dockerfile_for_custom_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """Shows generated Dockerfile for custom config."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "paude.json"
        config.write_text(json.dumps({"base": "python:3.11"}))

        show_dry_run({})

        captured = capsys.readouterr()
        assert "Generated Dockerfile" in captured.out
        assert "FROM ${BASE_IMAGE}" in captured.out

    def test_shows_all_flags(self, capsys: pytest.CaptureFixture[str]):
        """Shows all flags."""
        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run(
                    {
                        "yolo": True,
                        "allow_network": True,
                        "rebuild": False,
                    }
                )

        captured = capsys.readouterr()
        assert "--yolo: True" in captured.out
        assert "--allow-network: True" in captured.out
        assert "--rebuild: False" in captured.out
