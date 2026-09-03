"""Tests for user config loading and layered config resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from paude.config.models import PaudeConfig
from paude.config.resolver import ResolvedCreateOptions, resolve_create_options
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
        assert result.allowed_endpoints == []

    def test_loads_allowed_endpoints(self, tmp_path: Path) -> None:
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {"defaults": {"allowed-endpoints": ["api.example.com:8443"]}}
            )
        )

        assert load_user_defaults(config).allowed_endpoints == [
            "api.example.com:8443"
        ]

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

    def test_loads_agent_provider_mapping(self, tmp_path: Path):
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {
                    "defaults": {
                        "agents": ["claude", "codex"],
                        "providers": ["anthropic", "openai"],
                        "agent-providers": {
                            "claude": "anthropic",
                            "codex": "openai",
                        },
                    }
                }
            )
        )

        result = load_user_defaults(config)

        assert result.agent_providers == {
            "claude": "anthropic",
            "codex": "openai",
        }


class TestResolveCreateOptions:
    """Tests for resolve_create_options."""

    def _resolve(self, **kwargs: Any) -> ResolvedCreateOptions:
        defaults: dict[str, Any] = {
            "cli_backend": None,
            "cli_agent": None,
            "cli_yolo": None,
            "cli_git": None,
            "cli_platform": None,
            "cli_gpu": None,
            "cli_allowed_domains": None,
            "cli_allowed_endpoints": None,
            "project_config": None,
            "user_defaults": UserDefaults(),
        }
        defaults.update(kwargs)
        return resolve_create_options(**defaults)

    def test_builtin_defaults(self):
        """Returns built-in defaults when nothing is configured."""
        result = self._resolve()
        assert result.backend.value == "podman"
        assert result.allowed_endpoints == []
        assert result.backend.source == "built-in"
        assert result.agent.value == "claude"
        assert result.yolo.value is False
        assert result.git.value is False

    def test_endpoints_merge_and_cli_override(self) -> None:
        user = UserDefaults(allowed_endpoints=["user.example:8000"])
        project = PaudeConfig(
            create_allowed_endpoints=["project.example:9000"]
        )

        merged = self._resolve(user_defaults=user, project_config=project)
        assert merged.allowed_endpoints == [
            "user.example:8000",
            "project.example:9000",
        ]

        overridden = self._resolve(
            user_defaults=user,
            project_config=project,
            cli_allowed_endpoints=["cli.example:7000"],
        )
        assert overridden.allowed_endpoints == ["cli.example:7000"]

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


class TestResolveAgentsAndProviders:
    """Tests for list-valued agents/providers resolution."""

    def _resolve(self, **kwargs: Any) -> ResolvedCreateOptions:
        defaults: dict[str, Any] = {
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

    def test_builtin_agents_default(self):
        """Defaults to a single-item [claude] agents list."""
        result = self._resolve()
        assert result.agents == ["claude"]
        assert result.agent.value == "claude"
        assert result.agents_provenance == [(["claude"], "built-in")]

    def test_cli_agents_list(self):
        """--agents populates the list; first is the primary scalar."""
        result = self._resolve(cli_agents=["gascity", "claude", "codex"])
        assert result.agents == ["gascity", "claude", "codex"]
        assert result.agent.value == "gascity"
        assert result.agent.source == "cli"
        assert result.agents_provenance == [(["gascity", "claude", "codex"], "cli")]

    def test_singular_agent_alias(self):
        """--agent behaves as a single-item --agents list."""
        result = self._resolve(cli_agent="gascity")
        assert result.agents == ["gascity"]
        assert result.agent.value == "gascity"

    def test_agent_and_agents_conflict(self):
        """Passing both --agent and --agents raises a clear error."""
        with pytest.raises(ValueError, match="not both"):
            self._resolve(cli_agent="claude", cli_agents=["codex"])

    def test_provider_and_providers_are_independent(self):
        """Singular provider maps primary while plural enables credentials."""
        result = self._resolve(
            cli_provider="vertex", cli_providers=["vertex", "openai"]
        )
        assert result.agent_providers == [("claude", "vertex")]
        assert result.providers == ["vertex", "openai"]

    def test_duplicate_agents_rejected(self):
        """Duplicate installed agents are rejected instead of shifted."""
        with pytest.raises(ValueError, match="Duplicate agent"):
            self._resolve(cli_agents=["claude", "claude", "codex"])

    def test_unknown_agent_rejected(self):
        """An unknown agent name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown agent"):
            self._resolve(cli_agents=["not-a-real-agent"])

    def test_invalid_primary_provider_rejected(self):
        """An unsupported primary agent/provider combination raises."""
        with pytest.raises(ValueError, match="does not support provider"):
            self._resolve(cli_agents=["codex"], cli_provider="vertex")

    def test_per_agent_providers_derived(self):
        """Named mappings configure agents independently of credentials."""
        result = self._resolve(
            cli_agents=["gascity", "claude", "codex"],
            cli_providers=["vertex", "chatgpt"],
            cli_agent_providers={"codex": "chatgpt"},
        )
        assert result.agent_providers == [
            ("gascity", "vertex"),
            ("claude", "vertex"),
            ("codex", "chatgpt"),
        ]
        # The explicit pool keeps its provenance and order.
        assert result.providers == ["vertex", "chatgpt"]
        assert result.providers_provenance == [(["vertex", "chatgpt"], "cli")]

    def test_provider_credentials_are_deduplicated(self):
        """Credential providers are an unordered deduplicated set."""
        result = self._resolve(
            cli_agents=["claude", "codex"],
            cli_providers=["vertex", "vertex", "chatgpt"],
        )
        assert result.providers == ["vertex", "chatgpt"]

    def test_explicit_credentials_must_cover_defaults(self):
        """An exact credential set must cover default mappings too."""
        with pytest.raises(ValueError, match="missing: chatgpt"):
            self._resolve(cli_agents=["claude", "codex"], cli_providers=["vertex"])

    def test_providers_default_from_agent_defaults(self):
        """With no --providers, each agent's default provider is derived."""
        result = self._resolve(cli_agents=["claude"])
        assert result.providers == ["vertex"]
        assert result.agent_providers == [("claude", "vertex")]
        assert result.provider.value == "vertex"

    def test_agents_from_user_defaults(self):
        """Agents resolve from user defaults when no CLI value is given."""
        user = UserDefaults(agents=["gemini", "codex"])
        result = self._resolve(user_defaults=user)
        assert result.agents == ["gemini", "codex"]
        assert result.agents_provenance == [(["gemini", "codex"], "user defaults")]

    def test_cli_agents_override_user_defaults(self):
        """CLI --agents overrides user-default agents."""
        user = UserDefaults(agents=["gemini"])
        result = self._resolve(cli_agents=["claude"], user_defaults=user)
        assert result.agents == ["claude"]
        assert result.agents_provenance == [(["claude"], "cli")]

    def test_project_agents_override_user(self):
        """Project config agents override user defaults."""
        user = UserDefaults(agents=["gemini"])
        project = PaudeConfig(create_agents=["claude", "codex"])
        result = self._resolve(user_defaults=user, project_config=project)
        assert result.agents == ["claude", "codex"]
        assert result.agents_provenance == [(["claude", "codex"], "paude.json")]

    def test_project_singular_agent_alias(self):
        """Project singular create_agent acts as a one-item list."""
        project = PaudeConfig(create_agent="cursor")
        result = self._resolve(project_config=project)
        assert result.agents == ["cursor"]
        assert result.agents_provenance == [(["cursor"], "paude.json")]

    def test_empty_agent_rejected_cleanly(self):
        """An explicit empty-string --agent raises ValueError, not IndexError."""
        with pytest.raises(ValueError, match="Agent name cannot be empty"):
            self._resolve(cli_agent="")

    def test_empty_agent_from_user_defaults_rejected_cleanly(self):
        """An explicit empty-string agent in user defaults raises ValueError."""
        user = UserDefaults(agent="")
        with pytest.raises(ValueError, match="Agent name cannot be empty"):
            self._resolve(user_defaults=user)

    def test_extra_credential_providers_are_retained(self):
        """Credential providers need not be used by an agent mapping."""
        result = self._resolve(
            cli_agents=["claude", "codex"],
            cli_providers=["vertex", "chatgpt", "openai"],
        )
        assert result.agent_providers == [
            ("claude", "vertex"),
            ("codex", "chatgpt"),
        ]
        assert result.providers == ["vertex", "chatgpt", "openai"]

    def test_invalid_secondary_provider_rejected(self):
        """An unsupported named mapping fails for a secondary agent."""
        with pytest.raises(ValueError, match="does not support provider"):
            self._resolve(
                cli_agents=["claude", "codex"],
                cli_agent_providers={"codex": "anthropic"},
            )

    def test_mapping_for_uninstalled_agent_rejected(self):
        with pytest.raises(ValueError, match="not installed"):
            self._resolve(
                cli_agents=["claude"], cli_agent_providers={"codex": "openai"}
            )

    def test_mapping_highest_layer_replaces_lower_mapping(self):
        user = UserDefaults(
            agents=["claude", "codex"],
            agent_providers={"claude": "anthropic", "codex": "openai"},
        )
        project = PaudeConfig(
            create_agents=["claude", "codex"],
            create_agent_providers={"codex": "chatgpt"},
        )

        result = self._resolve(user_defaults=user, project_config=project)

        assert result.agent_providers == [
            ("claude", "vertex"),
            ("codex", "chatgpt"),
        ]

    def test_singular_and_mapping_conflict_in_project(self):
        project = PaudeConfig(
            create_provider="vertex",
            create_agent_providers={"claude": "anthropic"},
        )
        with pytest.raises(ValueError, match="not both"):
            self._resolve(project_config=project)

    def test_empty_provider_rejected_cleanly(self):
        """An explicit empty-string --provider raises ValueError, not a silent default."""
        with pytest.raises(ValueError, match="Provider name cannot be empty"):
            self._resolve(cli_agents=["claude"], cli_provider="")

    def test_empty_provider_from_user_defaults_rejected_cleanly(self):
        """An explicit empty-string provider in user defaults raises ValueError."""
        user = UserDefaults(provider="")
        with pytest.raises(ValueError, match="Provider name cannot be empty"):
            self._resolve(user_defaults=user)


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


class TestUserDefaultsAgentsProviders:
    """Tests for agents/providers lists in user defaults."""

    def test_agents_list_loads_from_json(self, tmp_path: Path):
        """agents/providers lists load from JSON config."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {"defaults": {"agents": ["gascity", "claude"], "providers": ["vertex"]}}
            )
        )

        result = load_user_defaults(config)
        assert result.agents == ["gascity", "claude"]
        assert result.providers == ["vertex"]

    def test_agents_default_to_empty(self, tmp_path: Path):
        """agents/providers default to empty lists when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "podman"}}))

        result = load_user_defaults(config)
        assert result.agents == []
        assert result.providers == []

    def test_agents_non_strings_dropped(self, tmp_path: Path):
        """Non-string entries in agents/providers are dropped."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps({"defaults": {"agents": ["claude", 42, None, "codex"]}})
        )

        result = load_user_defaults(config)
        assert result.agents == ["claude", "codex"]
