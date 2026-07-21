"""Tests for user config loading and layered config resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paude.config.models import PaudeConfig
from paude.config.resolver import resolve_create_options
from paude.config.user_config import UserDefaults, load_user_defaults


class TestLoadUserDefaults:
    """Tests for load_user_defaults."""

    def test_returns_empty_when_file_missing(self, tmp_path: Path):
        """Returns empty defaults when file does not exist."""
        result = load_user_defaults(tmp_path / "nonexistent.json")
        assert result.backend is None
        assert result.agent is None
        assert result.yolo is None
        assert result.git is None
        assert result.allowed_domains == []

    def test_warns_on_unknown_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Warns about unknown keys in defaults."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps({"defaults": {"unknown-key": "value", "also-bad": 42}})
        )

        load_user_defaults(config)
        captured = capsys.readouterr()
        assert "Unknown key 'also-bad'" in captured.err
        assert "Unknown key 'unknown-key'" in captured.err

    def test_handles_invalid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Returns empty defaults on invalid JSON."""
        config = tmp_path / "defaults.json"
        config.write_text("{ invalid json }")

        result = load_user_defaults(config)
        assert result.backend is None
        captured = capsys.readouterr()
        assert "Cannot read" in captured.err

    def test_handles_invalid_defaults_type(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Returns empty defaults when 'defaults' is not an object."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": "not-a-dict"}))

        result = load_user_defaults(config)
        assert result.backend is None
        captured = capsys.readouterr()
        assert "not an object" in captured.err


class TestResolveCreateOptions:
    """Tests for resolve_create_options."""

    def _resolve(self, **kwargs):
        defaults = {
            "cli_backend": None,
            "cli_agent": None,
            "cli_yolo": None,
            "cli_git": None,
            "cli_platform": None,
            "cli_gpu": None,
            "cli_allowed_domains": None,
            "project_config": None,
            "user_defaults": UserDefaults(),
        }
        defaults.update(kwargs)
        return resolve_create_options(**defaults)

    def test_builtin_defaults(self):
        """Returns built-in defaults when nothing is configured."""
        result = self._resolve()
        assert result.backend.value == "podman"
        assert result.backend.source == "built-in"
        assert result.agent.value == "claude"
        assert result.yolo.value is False
        assert result.git.value is False

    def test_project_overrides_user(self):
        """Project config overrides user defaults for agent."""
        user = UserDefaults(agent="gemini")
        project = PaudeConfig(create_agent="cursor")

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.agent.value == "cursor"
        assert result.agent.source == "paude.json"

    def test_domain_merge_user_and_project(self):
        """Domains merge (union) from user defaults and project config."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=[".vllm.ai", ".openai.com"])

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.allowed_domains == [
            "default",
            "golang",
            ".vllm.ai",
            ".openai.com",
        ]
        assert len(result.allowed_domains_provenance) == 2
        assert result.allowed_domains_provenance[0][1] == "user defaults"
        assert result.allowed_domains_provenance[1][1] == "paude.json"

    def test_domain_merge_deduplicates(self):
        """Domain merge removes duplicates."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=["golang", ".vllm.ai"])

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.allowed_domains == ["default", "golang", ".vllm.ai"]

    def test_cli_domains_override_merged(self):
        """CLI --allowed-domains replaces all merged domains."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=[".vllm.ai"])

        result = self._resolve(
            cli_allowed_domains=["rust"],
            user_defaults=user,
            project_config=project,
        )
        assert result.allowed_domains == ["rust"]
        assert len(result.allowed_domains_provenance) == 1
        assert result.allowed_domains_provenance[0][1] == "cli"

    def test_no_domains_configured(self):
        """Returns empty domains when nothing is configured."""
        result = self._resolve()
        assert result.allowed_domains == []
        assert result.allowed_domains_provenance == []

    def test_gpu_defaults_to_none(self):
        """GPU defaults to None when not configured."""
        result = self._resolve()
        assert result.gpu.value is None
        assert result.gpu.source == "built-in"

    def test_gpu_from_user_defaults(self):
        """GPU resolves from user defaults."""
        user = UserDefaults(gpu="all")
        result = self._resolve(user_defaults=user)
        assert result.gpu.value == "all"
        assert result.gpu.source == "user defaults"

    def test_gpu_cli_overrides_user(self):
        """CLI --gpu overrides user defaults."""
        user = UserDefaults(gpu="all")
        result = self._resolve(cli_gpu="device=0", user_defaults=user)
        assert result.gpu.value == "device=0"
        assert result.gpu.source == "cli"

    def test_gpu_no_gpu_overrides_user(self):
        """--no-gpu (empty string) overrides user default."""
        user = UserDefaults(gpu="all")
        result = self._resolve(cli_gpu="", user_defaults=user)
        assert result.gpu.value == ""
        assert result.gpu.source == "cli"


class TestUserDefaultsGpu:
    """Tests for GPU field in user defaults."""

    def test_gpu_field_loads_from_json(self, tmp_path: Path):
        """GPU field loads from JSON config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"gpu": "all"}}))

        result = load_user_defaults(config)
        assert result.gpu == "all"

    def test_gpu_device_spec_loads(self, tmp_path: Path):
        """GPU device spec loads correctly."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"gpu": "device=0,1"}}))

        result = load_user_defaults(config)
        assert result.gpu == "device=0,1"

    def test_gpu_defaults_to_none(self, tmp_path: Path):
        """GPU defaults to None when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "podman"}}))

        result = load_user_defaults(config)
        assert result.gpu is None


class TestUserDefaultsOtelEndpoint:
    """Tests for otel-endpoint in user defaults."""

    def test_otel_endpoint_loads_from_json(self, tmp_path: Path):
        """otel-endpoint field loads from JSON config."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps({"defaults": {"otel-endpoint": "http://collector:4318"}})
        )

        result = load_user_defaults(config)
        assert result.otel_endpoint == "http://collector:4318"

    def test_otel_endpoint_defaults_to_none(self, tmp_path: Path):
        """otel-endpoint defaults to None when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "podman"}}))

        result = load_user_defaults(config)
        assert result.otel_endpoint is None
