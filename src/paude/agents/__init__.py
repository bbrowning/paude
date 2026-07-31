"""Agent abstraction for CLI coding agents."""

from __future__ import annotations

from paude.agents.base import Agent, AgentComposition, AgentConfig
from paude.agents.claude import ClaudeAgent
from paude.agents.codex import CodexAgent
from paude.agents.cursor import CursorAgent
from paude.agents.gascity import GascityAgent
from paude.agents.gemini import GeminiAgent
from paude.agents.openclaw import OpenClawAgent
from paude.agents.opencode import OpenCodeAgent

__all__ = [
    "Agent",
    "AgentComposition",
    "AgentConfig",
    "ClaudeAgent",
    "CodexAgent",
    "CursorAgent",
    "GascityAgent",
    "GeminiAgent",
    "OpenCodeAgent",
    "OpenClawAgent",
    "get_agent",
    "get_agents",
    "list_agents",
]

_REGISTRY: dict[str, type] = {
    "claude": ClaudeAgent,
    "codex": CodexAgent,
    "cursor": CursorAgent,
    "gascity": GascityAgent,
    "gemini": GeminiAgent,
    "opencode": OpenCodeAgent,
    "openclaw": OpenClawAgent,
}


def get_agent(name: str, provider: str | None = None) -> Agent:
    """Get an agent instance by name.

    Args:
        name: Agent name (e.g., "claude").
        provider: Inference provider name (e.g., "vertex", "openai"),
            or None for the agent's default provider.

    Returns:
        Agent instance.

    Raises:
        ValueError: If agent name is not registered or provider is invalid.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")
    return cls(provider=provider)  # type: ignore[no-any-return]


def get_agents(
    names: list[str],
    providers: dict[str, str] | None = None,
) -> AgentComposition:
    """Expand requested agent names into a composed install set.

    Each requested agent is expanded into itself plus its bundled agents
    (recursively), deduplicated by name while preserving first-seen order. The
    primary agent is the one named first in ``names``.

    Each agent instance is built with its own default provider unless an
    override is supplied for it in ``providers``.

    Args:
        names: Requested agent names, most important first. ``names[0]`` becomes
            the primary agent.
        providers: Optional mapping of agent name -> provider override. Agents
            not present in the mapping use their own default provider.

    Returns:
        An AgentComposition holding the primary agent and the ordered,
        deduplicated install set.

    Raises:
        ValueError: If ``names`` is empty or any agent name is unknown.
    """
    if not names:
        raise ValueError("get_agents requires at least one agent name")

    provider_overrides = providers or {}
    ordered: list[Agent] = []
    seen: set[str] = set()

    def expand(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        agent = get_agent(name, provider=provider_overrides.get(name))
        ordered.append(agent)
        for bundled in agent.config.bundled_agents:
            expand(bundled)

    for name in names:
        expand(name)

    return AgentComposition(primary=ordered[0], agents=ordered)


def list_agents() -> list[str]:
    """List all registered agent names.

    Returns:
        Sorted list of agent names.
    """
    return sorted(_REGISTRY.keys())
