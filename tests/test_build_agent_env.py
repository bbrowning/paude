"""Tests for build_agent_env() in shared.py."""

from __future__ import annotations

from paude.agents.base import AgentConfig
from paude.backends.shared import build_agent_env


def _make_config(**overrides: object) -> AgentConfig:
    """Create a minimal AgentConfig with overrides."""
    defaults: dict[str, object] = {
        "name": "claude",
        "display_name": "Claude Code",
        "process_name": "claude",
        "session_name": "claude",
        "install_script": "curl -fsSL https://claude.ai/install.sh | bash",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


class TestBuildAgentEnvSeedPaths:
    """Tests for PAUDE_AGENT_SEED_DIR and PAUDE_AGENT_SEED_FILE env vars."""

    def test_claude_config_seed_paths(self) -> None:
        """Claude-like config produces correct seed paths."""
        config = _make_config(
            name="claude",
            config_file_name=".claude.json",
        )
        env = build_agent_env(config)
        assert env["PAUDE_AGENT_SEED_DIR"] == "/tmp/claude.seed"
        assert env["PAUDE_AGENT_SEED_FILE"] == "/tmp/claude.json.seed"

    def test_no_config_file_produces_empty_seed_file(self) -> None:
        """Agent with no config_file_name produces empty PAUDE_AGENT_SEED_FILE."""
        config = _make_config(
            name="gemini",
            config_file_name=None,
        )
        env = build_agent_env(config)
        assert env["PAUDE_AGENT_SEED_DIR"] == "/tmp/gemini.seed"
        assert env["PAUDE_AGENT_SEED_FILE"] == ""

    def test_seed_dir_uses_agent_name(self) -> None:
        """Seed dir is derived from agent name, not config dir."""
        config = _make_config(
            name="codex",
            config_dir_name=".codex-config",
            config_file_name=".codex.json",
        )
        env = build_agent_env(config)
        assert env["PAUDE_AGENT_SEED_DIR"] == "/tmp/codex.seed"
        assert env["PAUDE_AGENT_SEED_FILE"] == "/tmp/codex.json.seed"
