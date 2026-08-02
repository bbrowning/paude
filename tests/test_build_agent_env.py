"""Tests for build_agent_env() in shared.py."""

from __future__ import annotations

from paude.agents.base import AgentConfig
from paude.backends.session_env import build_agent_env


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


def test_build_agent_env_includes_all_persistent_dirs() -> None:
    config = _make_config(extra_persistent_dir_names=[".agents", ".local/share/test"])

    env = build_agent_env(config)

    assert env["PAUDE_AGENT_CONFIG_DIRS"] == ".claude .agents .local/share/test"
