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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Shows 'none' when no config."""
        monkeypatch.chdir(tmp_path)
        show_dry_run({})

        captured = capsys.readouterr()
        assert "Configuration: none" in captured.out

    def test_shows_config_file_when_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Shows config file when present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "paude.json"
        config.write_text(json.dumps({"base": "python:3.11"}))

        show_dry_run({})

        captured = capsys.readouterr()
        assert "paude.json" in captured.out

    def test_shows_packages_when_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        """Shows packages when present."""
        monkeypatch.chdir(tmp_path)
        config = tmp_path / "paude.json"
        config.write_text(
            json.dumps({"base": "python:3.11", "packages": ["git", "vim"]})
        )

        show_dry_run({})

        captured = capsys.readouterr()
        assert "git" in captured.out
        assert "vim" in captured.out

    def test_shows_generated_dockerfile_for_custom_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
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
                        "allowed_domains": None,  # None = unrestricted
                        "rebuild": False,
                    }
                )

        captured = capsys.readouterr()
        assert "--yolo: True" in captured.out
        assert "--allowed-domains: unrestricted" in captured.out
        assert "--rebuild: False" in captured.out

    def test_shows_allowed_domains_list(self, capsys: pytest.CaptureFixture[str]):
        """Shows allowed domains when set."""
        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run(
                    {
                        "allowed_domains": [".googleapis.com", ".google.com"],
                    }
                )

        captured = capsys.readouterr()
        assert "--allowed-domains:" in captured.out

    def test_shows_gpu_when_set(self, capsys: pytest.CaptureFixture[str]):
        """Shows gpu in resolved output when set."""
        from paude.config.resolver import ResolvedCreateOptions, SettingValue

        resolved = ResolvedCreateOptions(
            gpu=SettingValue("all", "cli"),
        )

        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run({}, resolved=resolved)

        captured = capsys.readouterr()
        assert "gpu: all" in captured.out
        assert "(cli)" in captured.out

    def test_hides_gpu_when_none(self, capsys: pytest.CaptureFixture[str]):
        """Does not show gpu line when gpu is None."""
        from paude.config.resolver import ResolvedCreateOptions

        resolved = ResolvedCreateOptions()

        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run({}, resolved=resolved)

        captured = capsys.readouterr()
        assert "gpu:" not in captured.out

    def test_shows_agents_providers_lists(self, capsys: pytest.CaptureFixture[str]):
        """Shows agents/providers lists, provenance, and per-agent mapping."""
        from paude.config.resolver import ResolvedCreateOptions, SettingValue

        resolved = ResolvedCreateOptions(
            agent=SettingValue("gascity", "cli"),
            provider=SettingValue("vertex", "cli"),
            agents=["gascity", "claude", "codex"],
            agents_provenance=[(["gascity", "claude", "codex"], "cli")],
            providers=["vertex", "chatgpt"],
            providers_provenance=[(["vertex", "chatgpt"], "cli")],
            agent_providers=[
                ("gascity", "vertex"),
                ("claude", "vertex"),
                ("codex", "chatgpt"),
            ],
        )

        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run({}, resolved=resolved)

        captured = capsys.readouterr()
        assert "agents: gascity, claude, codex" in captured.out
        assert "credential providers: vertex, chatgpt" in captured.out
        assert "agent-provider mappings:" in captured.out
        assert "gascity -> vertex" in captured.out
        assert "codex -> chatgpt" in captured.out

    def test_provider_scalar_matches_agent_default_when_unset(
        self, capsys: pytest.CaptureFixture[str]
    ):
        """Legacy provider scalar mirrors the derived default, not '(not set)'."""
        from paude.config.resolver import ResolvedCreateOptions

        resolved = ResolvedCreateOptions(
            agents=["claude"],
            agent_providers=[("claude", "vertex")],
        )

        with patch("paude.dry_run.Path.cwd") as mock_cwd:
            mock_cwd.return_value = Path("/test")
            with patch("paude.dry_run.detect_config") as mock_detect:
                mock_detect.return_value = None
                show_dry_run({}, resolved=resolved)

        captured = capsys.readouterr()
        assert "provider: vertex" in captured.out
        assert "provider: (not set)" not in captured.out
