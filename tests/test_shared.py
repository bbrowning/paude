"""Tests for paude.backends.shared module."""

from __future__ import annotations

from pathlib import Path

import pytest

from paude.agents.claude import ClaudeAgent
from paude.backends.base import SessionConfig
from paude.backends.shared import (
    PROXY_GCP_ADC_ENV,
    build_session_env,
    gather_proxy_credentials,
    network_name,
    pod_name,
    proxy_resource_name,
    pvc_name,
    resource_name,
    volume_name,
)


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

        env, _args = build_session_env(config, agent)

        assert "PAUDE_HOST_WORKSPACE" not in env

    def test_suppress_prompts_always_set(self) -> None:
        """PAUDE_SUPPRESS_PROMPTS is always '1' regardless of proxy_name."""
        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()

        env, _args = build_session_env(config, agent)

        assert env["PAUDE_SUPPRESS_PROMPTS"] == "1"


class TestBuildSessionEnvProxyCredentials:
    """Tests for dummy credential injection when proxy is active."""

    def test_proxy_active_sets_dummy_api_key(self) -> None:
        """Secret env vars are set to proxy-managed sentinel when proxy active."""
        from paude.backends.shared import PROXY_MANAGED_CREDENTIAL

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
        from paude.backends.shared import PROXY_MANAGED_CREDENTIAL

        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()
        env, _args = build_session_env(config, agent, proxy_name="10.89.0.2")

        assert env["GH_TOKEN"] == PROXY_MANAGED_CREDENTIAL

    def test_no_proxy_does_not_set_dummy_credentials(self) -> None:
        """Secret env vars are NOT set when no proxy is active."""
        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/project"),
            image="test-image",
        )
        agent = ClaudeAgent()
        env, _args = build_session_env(config, agent, proxy_name=None)

        assert "GH_TOKEN" not in env
        for var in agent.config.secret_env_vars:
            assert var not in env


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


class TestNamingHelpers:
    """Tests for resource naming helper functions."""

    def test_resource_name(self) -> None:
        assert resource_name("my-session") == "paude-my-session"

    def test_proxy_resource_name(self) -> None:
        assert proxy_resource_name("my-session") == "paude-proxy-my-session"

    def test_pod_name(self) -> None:
        assert pod_name("my-session") == "paude-my-session-0"

    def test_pvc_name(self) -> None:
        assert pvc_name("my-session") == "workspace-paude-my-session-0"

    def test_volume_name(self) -> None:
        assert volume_name("my-session") == "paude-my-session-workspace"

    def test_network_name(self) -> None:
        assert network_name("my-session") == "paude-net-my-session"
