"""Tests for the provider abstraction module."""

from __future__ import annotations

import pytest

from paude.providers.agent_providers import (
    AGENT_PROVIDERS,
    DEFAULT_PROVIDER,
    AgentProviderConfig,
    resolve_agent_provider,
    supported_providers,
)
from paude.providers.base import ProviderConfig, get_provider, list_providers


class TestProviderRegistry:
    """Tests for provider registry functions."""

    def test_get_provider_vertex(self) -> None:
        p = get_provider("vertex")
        assert isinstance(p, ProviderConfig)
        assert p.name == "vertex"
        assert p.display_name == "Vertex AI"

    def test_get_provider_openai(self) -> None:
        p = get_provider("openai")
        assert p.name == "openai"
        assert "OPENAI_API_KEY" in p.secret_env_vars

    def test_get_provider_anthropic(self) -> None:
        p = get_provider("anthropic")
        assert "ANTHROPIC_API_KEY" in p.secret_env_vars

    def test_get_provider_cursor(self) -> None:
        p = get_provider("cursor")
        assert "CURSOR_API_KEY" in p.secret_env_vars

    def test_get_provider_google(self) -> None:
        p = get_provider("google")
        assert "GOOGLE_CLOUD_PROJECT" in p.passthrough_env_vars

    def test_get_provider_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider 'nonexistent'"):
            get_provider("nonexistent")

    def test_list_providers(self) -> None:
        providers = list_providers()
        assert "vertex" in providers
        assert "openai" in providers
        assert "anthropic" in providers
        assert providers == sorted(providers)

    def test_vertex_has_no_secrets(self) -> None:
        p = get_provider("vertex")
        assert p.secret_env_vars == []

    def test_vertex_has_passthrough_prefixes(self) -> None:
        p = get_provider("vertex")
        assert "CLOUDSDK_AUTH_" in p.passthrough_env_prefixes

    def test_vertex_domain_aliases(self) -> None:
        p = get_provider("vertex")
        assert "vertexai" in p.domain_aliases

    def test_openai_domain_aliases(self) -> None:
        p = get_provider("openai")
        assert "openai" in p.domain_aliases


class TestAgentProviderResolution:
    """Tests for resolve_agent_provider."""

    def test_resolve_claude_vertex(self) -> None:
        provider, agent_cfg = resolve_agent_provider("claude", "vertex")
        assert provider.name == "vertex"
        assert isinstance(agent_cfg, AgentProviderConfig)
        assert agent_cfg.extra_env_vars.get("CLAUDE_CODE_USE_VERTEX") == "1"

    def test_resolve_claude_anthropic(self) -> None:
        provider, agent_cfg = resolve_agent_provider("claude", "anthropic")
        assert provider.name == "anthropic"
        assert "CLAUDE_CODE_USE_VERTEX" not in agent_cfg.extra_env_vars

    def test_resolve_claude_default(self) -> None:
        provider, _ = resolve_agent_provider("claude")
        assert provider.name == "vertex"

    def test_resolve_openclaw_vertex(self) -> None:
        _, agent_cfg = resolve_agent_provider("openclaw", "vertex")
        assert agent_cfg.model_config["primary"] == "anthropic-vertex/claude-sonnet-4-6"

    def test_resolve_openclaw_openai(self) -> None:
        _, agent_cfg = resolve_agent_provider("openclaw", "openai")
        assert agent_cfg.model_config["primary"] == "openai/gpt-5.4-mini"

    def test_resolve_openclaw_anthropic(self) -> None:
        _, agent_cfg = resolve_agent_provider("openclaw", "anthropic")
        assert agent_cfg.model_config["primary"] == "anthropic/claude-opus-4-6"

    def test_resolve_cursor_cursor(self) -> None:
        provider, _ = resolve_agent_provider("cursor", "cursor")
        assert provider.name == "cursor"

    def test_resolve_gemini_google(self) -> None:
        provider, _ = resolve_agent_provider("gemini", "google")
        assert provider.name == "google"

    def test_invalid_provider_for_agent_raises(self) -> None:
        with pytest.raises(ValueError, match="does not support provider 'openai'"):
            resolve_agent_provider("cursor", "openai")

    def test_invalid_provider_lists_valid(self) -> None:
        with pytest.raises(ValueError, match="Valid providers: cursor"):
            resolve_agent_provider("cursor", "openai")

    def test_unknown_agent_raises(self) -> None:
        with pytest.raises(ValueError, match="No provider configuration"):
            resolve_agent_provider("nonexistent", "vertex")

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            resolve_agent_provider("claude", "nonexistent")


class TestDefaultProviders:
    """Tests for DEFAULT_PROVIDER mapping."""

    def test_claude_default_is_vertex(self) -> None:
        assert DEFAULT_PROVIDER["claude"] == "vertex"

    def test_openclaw_default_is_vertex(self) -> None:
        assert DEFAULT_PROVIDER["openclaw"] == "vertex"

    def test_cursor_default_is_cursor(self) -> None:
        assert DEFAULT_PROVIDER["cursor"] == "cursor"

    def test_gemini_default_is_google(self) -> None:
        assert DEFAULT_PROVIDER["gemini"] == "google"

    def test_all_agents_have_defaults(self) -> None:
        for agent_name in AGENT_PROVIDERS:
            assert agent_name in DEFAULT_PROVIDER

    def test_all_defaults_are_valid(self) -> None:
        for agent_name, provider_name in DEFAULT_PROVIDER.items():
            # Should not raise
            resolve_agent_provider(agent_name, provider_name)


class TestSupportedProviders:
    """Tests for supported_providers helper."""

    def test_claude_providers(self) -> None:
        providers = supported_providers("claude")
        assert "vertex" in providers
        assert "anthropic" in providers

    def test_openclaw_providers(self) -> None:
        providers = supported_providers("openclaw")
        assert "vertex" in providers
        assert "openai" in providers
        assert "anthropic" in providers

    def test_unknown_agent_returns_empty(self) -> None:
        assert supported_providers("nonexistent") == []

    def test_results_are_sorted(self) -> None:
        for agent_name in AGENT_PROVIDERS:
            providers = supported_providers(agent_name)
            assert providers == sorted(providers)
