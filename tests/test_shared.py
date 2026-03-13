"""Tests for paude.backends.shared module."""

from __future__ import annotations

from pathlib import Path

from paude.agents.claude import ClaudeAgent
from paude.backends.base import SessionConfig
from paude.backends.shared import build_session_env


class TestBuildSessionEnv:
    """Tests for build_session_env()."""

    def test_includes_host_workspace(self) -> None:
        """PAUDE_HOST_WORKSPACE is set to config.workspace."""
        config = SessionConfig(
            name="test",
            workspace=Path("/Volumes/SourceCode/paude"),
            image="test-image",
        )
        agent = ClaudeAgent()

        env, _args = build_session_env(config, agent)

        assert env["PAUDE_HOST_WORKSPACE"] == "/Volumes/SourceCode/paude"

    def test_host_workspace_varies_with_config(self) -> None:
        """PAUDE_HOST_WORKSPACE reflects the actual workspace path."""
        config = SessionConfig(
            name="test",
            workspace=Path("/home/user/projects/myapp"),
            image="test-image",
        )
        agent = ClaudeAgent()

        env, _args = build_session_env(config, agent)

        assert env["PAUDE_HOST_WORKSPACE"] == "/home/user/projects/myapp"

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
