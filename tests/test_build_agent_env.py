"""Tests for build_agent_env() in shared.py."""

from __future__ import annotations

from paude.agents.base import AgentConfig


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
