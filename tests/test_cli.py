"""Tests for CLI argument parsing."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from paude.cli import app

runner = CliRunner()


def test_help_shows_help():
    """--help shows help and exits 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "paude - Run Claude Code" in result.stdout


def test_short_help_shows_help():
    """-h shows help and exits 0."""
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    assert "paude - Run Claude Code" in result.stdout


def test_version_shows_version():
    """--version shows version and exits 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "paude 0.4.0" in result.stdout


def test_short_version_shows_version():
    """-V shows version and exits 0."""
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert "paude 0.4.0" in result.stdout


def test_version_shows_development_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'development' when PAUDE_DEV=1."""
    monkeypatch.setenv("PAUDE_DEV", "1")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "development" in result.stdout
    assert "PAUDE_DEV=1" in result.stdout


def test_version_shows_installed_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'installed' when PAUDE_DEV=0."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "installed" in result.stdout


def test_version_shows_custom_registry(monkeypatch: pytest.MonkeyPatch):
    """--version shows custom registry when PAUDE_REGISTRY is set."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    monkeypatch.setenv("PAUDE_REGISTRY", "ghcr.io/custom")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ghcr.io/custom" in result.stdout


def test_dry_run_works():
    """--dry-run works and shows config info."""
    result = runner.invoke(app, ["--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.stdout


def test_dry_run_shows_no_config(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """--dry-run shows 'none' when no config file exists."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--dry-run"])
    assert result.exit_code == 0
    assert "Configuration: none" in result.stdout


def test_dry_run_shows_flag_states():
    """--dry-run shows flag states."""
    result = runner.invoke(app, ["--yolo", "--allow-network", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allow-network: True" in result.stdout


def test_yolo_flag_recognized():
    """--yolo flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["--yolo", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout


def test_allow_network_flag_recognized():
    """--allow-network flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["--allow-network", "--dry-run"])
    assert result.exit_code == 0
    assert "--allow-network: True" in result.stdout


def test_rebuild_flag_recognized():
    """--rebuild flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["--rebuild", "--dry-run"])
    assert result.exit_code == 0
    assert "--rebuild: True" in result.stdout


def test_help_shows_dry_run_option():
    """--help shows --dry-run option."""
    result = runner.invoke(app, ["--help"])
    assert "--dry-run" in result.stdout


def test_args_after_double_dash():
    """Arguments after -- are captured in claude_args (verified via dry-run)."""
    result = runner.invoke(app, ["--dry-run", "--", "-p", "hello"])
    assert result.exit_code == 0
    assert "claude_args: ['-p', 'hello']" in result.stdout


def test_multiple_flags_work_together():
    """Multiple flags work together (verified via dry-run)."""
    result = runner.invoke(app, ["--yolo", "--allow-network", "--rebuild", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allow-network: True" in result.stdout
    assert "--rebuild: True" in result.stdout


def test_backend_flag_recognized():
    """--backend flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["--backend=podman", "--dry-run"])
    assert result.exit_code == 0
    assert "--backend: podman" in result.stdout


def test_backend_openshift_shows_openshift_options():
    """--backend=openshift shows OpenShift-specific options."""
    result = runner.invoke(app, ["--backend=openshift", "--dry-run"])
    assert result.exit_code == 0
    assert "--backend: openshift" in result.stdout
    assert "--openshift-namespace:" in result.stdout


def test_help_shows_subcommands():
    """Help shows subcommands section."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "COMMANDS:" in result.stdout
    assert "sessions" in result.stdout
    assert "attach" in result.stdout
    assert "stop" in result.stdout
    assert "sync" in result.stdout
