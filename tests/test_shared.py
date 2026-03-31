"""Tests for paude.backends.shared module."""

from __future__ import annotations

from pathlib import Path

from paude.agents.claude import ClaudeAgent
from paude.backends.base import SessionConfig
from paude.backends.shared import (
    build_session_env,
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
