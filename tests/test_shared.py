"""Tests for shared backend utilities (naming, proxy_config, session_env)."""

from __future__ import annotations

from pathlib import Path

import pytest

from paude.agents import get_agents
from paude.agents.claude import ClaudeAgent
from paude.backends.base import SessionConfig
from paude.backends.naming import (
    network_name,
    proxy_resource_name,
    resource_name,
    volume_name,
)
from paude.backends.proxy_config import (
    PROXY_GCP_ADC_ENV,
    PROXY_MANAGED_CREDENTIAL,
    gather_proxy_credentials,
)
from paude.backends.session_env import build_session_env


class TestBuildSessionEnv:
    """Tests for build_session_env()."""

    def test_no_host_workspace_env(self) -> None:
        """PAUDE_HOST_WORKSPACE is no longer set."""
        config = SessionConfig(
            name="test",
            workspace=Path("/Volumes/SourceCode/paude"),
            image="test-image",
        )
        agent = ClaudeAgent()

        env, _args = build_session_env(config, agent, proxy_name="proxy-test")

        assert "PAUDE_HOST_WORKSPACE" not in env

    def test_suppress_prompts_always_set(self) -> None:
        """PAUDE_SUPPRESS_PROMPTS is always '1' regardless of proxy_name."""
        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()

        env, _args = build_session_env(config, agent, proxy_name="proxy-test")

        assert env["PAUDE_SUPPRESS_PROMPTS"] == "1"

    def test_composition_exports_all_runtime_metadata(self) -> None:
        composition = get_agents(
            ["gascity", "claude", "codex"],
            providers={"gascity": "vertex", "claude": "vertex", "codex": "chatgpt"},
            include_bundled=False,
        )
        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
            agent="gascity",
            agent_providers=[
                ("gascity", "vertex"),
                ("claude", "vertex"),
                ("codex", "chatgpt"),
            ],
            env={"CLAUDE_CODE_USE_VERTEX": "1", "CODEX_HOME": "/home/paude/.codex"},
        )

        env, _args = build_session_env(config, composition, proxy_name="proxy-test")

        assert env["PAUDE_AGENT_CONFIG_DIRS"] == ".gc .claude .codex .agents"
        assert env["PAUDE_AGENT_CONFIG_FILES"] == ".claude.json"
        assert env["PAUDE_AGENT_PROVIDERS"] == (
            "gascity=vertex,claude=vertex,codex=chatgpt"
        )
        assert env["PAUDE_CODEX_CHATGPT_MODE"] == "1"
        assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
        assert env["CODEX_HOME"] == "/home/paude/.codex"

    def test_extra_credential_provider_adds_proxy_placeholder(self) -> None:
        composition = get_agents(["claude"], include_bundled=False)
        config = SessionConfig(
            name="test",
            workspace=Path("/tmp/workspace"),
            image="image",
            credential_providers=["vertex", "openai"],
        )

        env, _args = build_session_env(config, composition, proxy_name="proxy")

        assert env["PAUDE_PROVIDERS"] == "vertex,openai"
        assert env["OPENAI_API_KEY"] == PROXY_MANAGED_CREDENTIAL


class TestBuildSessionEnvProxyCredentials:
    """Tests for dummy credential injection when proxy is active."""

    def test_proxy_active_sets_dummy_api_key(self) -> None:
        """Secret env vars are set to proxy-managed sentinel when proxy active."""
        from paude.backends.proxy_config import PROXY_MANAGED_CREDENTIAL

        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()
        env, _args = build_session_env(config, agent, proxy_name="10.89.0.2")

        for var in agent.config.secret_env_vars:
            assert env[var] == PROXY_MANAGED_CREDENTIAL

    def test_proxy_active_sets_dummy_gh_token(self) -> None:
        """GH_TOKEN is set to proxy-managed sentinel when proxy active."""
        from paude.backends.proxy_config import PROXY_MANAGED_CREDENTIAL

        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()
        env, _args = build_session_env(config, agent, proxy_name="10.89.0.2")

        assert env["GH_TOKEN"] == PROXY_MANAGED_CREDENTIAL

    def test_dummy_credentials_always_set(self) -> None:
        """Secret env vars are always set since proxy is always active."""
        from paude.backends.proxy_config import PROXY_MANAGED_CREDENTIAL

        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()
        env, _args = build_session_env(config, agent, proxy_name="proxy-test")

        assert env["GH_TOKEN"] == PROXY_MANAGED_CREDENTIAL
        for var in agent.config.secret_env_vars:
            assert env[var] == PROXY_MANAGED_CREDENTIAL


class TestGatherProxyCredentials:
    """Tests for gather_proxy_credentials()."""

    def test_includes_secret_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Agent secret env vars are included in proxy credentials."""
        monkeypatch.delenv("PAUDE_GITHUB_TOKEN", raising=False)
        agent = ClaudeAgent(provider="anthropic")
        # Set the secret env var that the anthropic provider defines
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-value")  # noqa: S105

        creds = gather_proxy_credentials(agent.config)

        assert "ANTHROPIC_API_KEY" in creds
        assert creds["ANTHROPIC_API_KEY"] == "test-key-value"  # noqa: S105

    def test_includes_gh_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GH_TOKEN is picked up from PAUDE_GITHUB_TOKEN env var."""
        monkeypatch.setenv("PAUDE_GITHUB_TOKEN", "test-token-value")  # noqa: S105
        agent = ClaudeAgent()

        creds = gather_proxy_credentials(agent.config)

        assert creds["GH_TOKEN"] == "test-token-value"  # noqa: S105

    def test_no_gh_token_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GH_TOKEN is absent when PAUDE_GITHUB_TOKEN is not set."""
        monkeypatch.delenv("PAUDE_GITHUB_TOKEN", raising=False)
        agent = ClaudeAgent()

        creds = gather_proxy_credentials(agent.config)

        assert "GH_TOKEN" not in creds

    def test_includes_gcp_adc_json_when_exists(self, tmp_path: Path) -> None:
        """GCP_ADC_JSON contains file content when GCP ADC path is provided."""
        agent = ClaudeAgent()
        adc_file = tmp_path / "adc.json"
        adc_file.write_text('{"type": "authorized_user"}')

        creds = gather_proxy_credentials(agent.config, gcp_adc_path=adc_file)

        assert creds[PROXY_GCP_ADC_ENV] == '{"type": "authorized_user"}'

    def test_no_gcp_adc_when_path_is_none(self) -> None:
        """GCP_ADC_JSON is absent when no ADC path is provided."""
        agent = ClaudeAgent()

        creds = gather_proxy_credentials(agent.config, gcp_adc_path=None)

        assert PROXY_GCP_ADC_ENV not in creds

    def test_chatgpt_mode_flag_set_for_codex_chatgpt_provider(self) -> None:
        """chatgpt_oauth_mode is True for a codex agent using the chatgpt provider."""
        from paude.agents.codex import CodexAgent

        creds = gather_proxy_credentials(CodexAgent(provider="chatgpt").config)

        assert creds.chatgpt_oauth_mode is True

    def test_chatgpt_mode_flag_set_for_composed_codex(self) -> None:
        composition = get_agents(
            ["gascity", "claude", "codex"],
            providers={"gascity": "vertex", "claude": "vertex", "codex": "chatgpt"},
            include_bundled=False,
        )

        creds = gather_proxy_credentials(composition)

        assert creds.chatgpt_oauth_mode is True

    def test_chatgpt_mode_flag_false_for_codex_openai_provider(self) -> None:
        """chatgpt_oauth_mode is False for a codex agent using the openai provider."""
        from paude.agents.codex import CodexAgent

        creds = gather_proxy_credentials(CodexAgent(provider="openai").config)

        assert creds.chatgpt_oauth_mode is False

    def test_chatgpt_mode_flag_set_for_opencode_chatgpt_provider(self) -> None:
        """chatgpt_oauth_mode is True for an opencode agent using the chatgpt provider."""
        from paude.agents.opencode import OpenCodeAgent

        creds = gather_proxy_credentials(OpenCodeAgent(provider="chatgpt").config)

        assert creds.chatgpt_oauth_mode is True

    def test_chatgpt_mode_flag_false_for_non_chatgpt_provider(self) -> None:
        """chatgpt_oauth_mode is False for agents not using the chatgpt provider."""
        creds = gather_proxy_credentials(ClaudeAgent().config)

        assert creds.chatgpt_oauth_mode is False

    def test_extra_credential_provider_collects_its_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "extra-provider-key")
        composition = get_agents(["claude"], include_bundled=False)

        creds = gather_proxy_credentials(
            composition, credential_providers=["vertex", "openai"]
        )

        assert creds["OPENAI_API_KEY"] == "extra-provider-key"

    def test_chatgpt_mode_follows_credential_set(self) -> None:
        composition = get_agents(
            ["codex"], providers={"codex": "openai"}, include_bundled=False
        )

        creds = gather_proxy_credentials(
            composition, credential_providers=["openai", "chatgpt"]
        )

        assert creds.chatgpt_oauth_mode is True


class TestNamingHelpers:
    """Tests for resource naming helper functions."""

    def test_resource_name(self) -> None:
        assert resource_name("my-session") == "paude-my-session"

    def test_proxy_resource_name(self) -> None:
        assert proxy_resource_name("my-session") == "paude-proxy-my-session"

    def test_volume_name(self) -> None:
        assert volume_name("my-session") == "paude-my-session-workspace"

    def test_network_name(self) -> None:
        assert network_name("my-session") == "paude-net-my-session"
