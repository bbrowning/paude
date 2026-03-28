"""Tests for environment variable builder."""

from __future__ import annotations

import pytest

from paude.environment import build_environment, build_proxy_environment


class TestBuildEnvironment:
    """Tests for build_environment."""

    def test_claude_code_use_vertex_in_env_vars(self):
        """CLAUDE_CODE_USE_VERTEX is set as a static env var (not passthrough)."""
        # With vertex provider (default), CLAUDE_CODE_USE_VERTEX is in env_vars
        # not passthrough_env_vars, so it's set by the agent's config directly
        from paude.agents import get_agent

        agent = get_agent("claude")
        assert agent.config.env_vars.get("CLAUDE_CODE_USE_VERTEX") == "1"

    def test_anthropic_vertex_project_id_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """ANTHROPIC_VERTEX_PROJECT_ID passed through when set."""
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "my-project")
        env = build_environment()
        assert env.get("ANTHROPIC_VERTEX_PROJECT_ID") == "my-project"

    def test_cloudsdk_auth_vars_collected(self, monkeypatch: pytest.MonkeyPatch):
        """CLOUDSDK_AUTH variables are collected."""
        monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", "/path/to/creds")
        monkeypatch.setenv("CLOUDSDK_AUTH_ACCESS_TOKEN", "token123")
        env = build_environment()
        assert env.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE") == "/path/to/creds"
        assert env.get("CLOUDSDK_AUTH_ACCESS_TOKEN") == "token123"

    def test_missing_passthrough_vars_not_included(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Missing passthrough env vars are not included, but static env vars are."""
        monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        env = build_environment()
        # CLAUDE_CODE_USE_VERTEX is a static env_var, always present
        assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
        # ANTHROPIC_VERTEX_PROJECT_ID is a passthrough var, absent when unset
        assert "ANTHROPIC_VERTEX_PROJECT_ID" not in env

    def test_gh_token_not_auto_propagated(self, monkeypatch: pytest.MonkeyPatch):
        """GH_TOKEN from host environment is NOT passed into the container.

        Security test: GH_TOKEN must only be injected explicitly via --github-token,
        never automatically from the host environment.
        """
        monkeypatch.setenv("GH_TOKEN", "secret123")
        env = build_environment()
        assert "GH_TOKEN" not in env


class TestBuildProxyEnvironment:
    """Tests for build_proxy_environment."""

    def test_includes_all_proxy_vars(self):
        """Proxy environment includes all proxy vars."""
        env = build_proxy_environment("paude-proxy")
        assert "HTTP_PROXY" in env
        assert "HTTPS_PROXY" in env
        assert "http_proxy" in env
        assert "https_proxy" in env
        assert "NO_PROXY" in env
        assert "no_proxy" in env

    def test_proxy_url_format(self):
        """Proxy URL has correct format."""
        env = build_proxy_environment("my-proxy")
        assert env["HTTP_PROXY"] == "http://my-proxy:3128"
        assert env["HTTPS_PROXY"] == "http://my-proxy:3128"

    def test_no_proxy_bypasses_localhost(self):
        """NO_PROXY includes localhost and 127.0.0.1."""
        env = build_proxy_environment("paude-proxy")
        assert env["NO_PROXY"] == "localhost,127.0.0.1"
        assert env["no_proxy"] == "localhost,127.0.0.1"
