"""Tests for environment variable builder."""

from __future__ import annotations

import pytest

from paude.environment import build_environment, build_proxy_environment


class TestBuildEnvironment:
    """Tests for build_environment."""

    def test_claude_code_use_vertex_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """CLAUDE_CODE_USE_VERTEX passed through when set."""
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        env = build_environment()
        assert env.get("CLAUDE_CODE_USE_VERTEX") == "1"

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

    def test_missing_env_vars_not_included(self, monkeypatch: pytest.MonkeyPatch):
        """Missing env vars are not included."""
        # Ensure these are not set
        monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        env = build_environment()
        assert "CLAUDE_CODE_USE_VERTEX" not in env
        assert "ANTHROPIC_VERTEX_PROJECT_ID" not in env


class TestBuildProxyEnvironment:
    """Tests for build_proxy_environment."""

    def test_includes_all_four_proxy_vars(self):
        """Proxy environment includes all 4 proxy vars."""
        env = build_proxy_environment("paude-proxy")
        assert "HTTP_PROXY" in env
        assert "HTTPS_PROXY" in env
        assert "http_proxy" in env
        assert "https_proxy" in env

    def test_proxy_url_format(self):
        """Proxy URL has correct format."""
        env = build_proxy_environment("my-proxy")
        assert env["HTTP_PROXY"] == "http://my-proxy:3128"
        assert env["HTTPS_PROXY"] == "http://my-proxy:3128"
