"""Integration tests for paude."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paude.cli import app

runner = CliRunner()


class TestFullFlow:
    """End-to-end tests with mocked podman."""

    def test_dry_run_with_default_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Full flow with default config in dry-run mode."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0
        assert "Configuration: none" in result.stdout

    def test_dry_run_with_devcontainer_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Full flow with devcontainer.json in dry-run mode."""
        monkeypatch.chdir(tmp_path)
        devcontainer = tmp_path / ".devcontainer.json"
        devcontainer.write_text(json.dumps({"image": "python:3.11-slim"}))

        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0
        assert "devcontainer" in result.stdout
        assert "python:3.11-slim" in result.stdout

    def test_dry_run_with_paude_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Full flow with paude.json in dry-run mode."""
        monkeypatch.chdir(tmp_path)
        paude_json = tmp_path / "paude.json"
        paude_json.write_text(
            json.dumps({"base": "node:22-slim", "packages": ["git", "make"]})
        )

        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0
        assert "paude" in result.stdout
        assert "node:22-slim" in result.stdout
        assert "git" in result.stdout

    def test_dry_run_shows_correct_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Dry-run mode shows correct output."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--dry-run"])
        assert result.exit_code == 0
        assert "Dry-run mode" in result.stdout
        assert "Workspace" in result.stdout

    def test_allow_network_flag_recognized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Allow-network flag is recognized in dry-run."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--allow-network", "--dry-run"])
        assert result.exit_code == 0
        assert "--allow-network: True" in result.stdout

    def test_yolo_flag_recognized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """YOLO flag is recognized in dry-run."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--yolo", "--dry-run"])
        assert result.exit_code == 0
        assert "--yolo: True" in result.stdout

    def test_rebuild_flag_recognized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Rebuild flag is recognized in dry-run."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--rebuild", "--dry-run"])
        assert result.exit_code == 0
        assert "--rebuild: True" in result.stdout
