"""Agent abstraction for CLI coding agents."""

from __future__ import annotations

from paude.agents.base import (
    Agent,
    AgentComposition,
    AgentConfig,
    compose_dockerfile_install_lines,
)
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
    "composed_dockerfile_install_lines",
    "get_agent",
    "get_agent_composition",
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


def get_agent_composition(agent: Agent) -> AgentComposition:
    """Build an install composition around an existing agent instance.

    Unlike :func:`get_agents`, the provided instance is used verbatim as the
    primary agent (preserving its resolved provider and config); only its
    bundled agents are resolved from the registry and appended in first-seen
    order. A single agent with no ``bundled_agents`` composes to just itself, so
    this is safe to call for every agent — including custom instances that are
    not in the registry.

    Args:
        agent: The primary agent instance.

    Returns:
        An AgentComposition holding ``agent`` as primary plus the ordered,
        deduplicated bundled-agent install set.
    """
    ordered: list[Agent] = [agent]
    seen: set[str] = {agent.config.name}

    def expand(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        bundled = get_agent(name)
        ordered.append(bundled)
        for nested in bundled.config.bundled_agents:
            expand(nested)

    for name in agent.config.bundled_agents:
        expand(name)

    return AgentComposition(primary=agent, agents=ordered)


def composed_dockerfile_install_lines(agent: Agent, container_home: str) -> list[str]:
    """Return Dockerfile install lines for an agent plus its bundled toolchains.

    This is the composition entry point for the image build path: the given
    instance is installed as the primary agent and every bundled toolchain
    (e.g. gascity's Claude Code + Gemini CLI) is stitched in via
    :func:`compose_dockerfile_install_lines`. Single agents compose to just
    their own lines, so every generator can call this uniformly.

    Args:
        agent: The primary agent instance.
        container_home: Home directory path inside the container.

    Returns:
        Combined, deduplicated list of Dockerfile instruction lines, ending with
        the canonical ``USER paude`` / ``WORKDIR`` footer.
    """
    composition = get_agent_composition(agent)
    return compose_dockerfile_install_lines(composition.agents, container_home)


def list_agents() -> list[str]:
    """List all registered agent names.

    Returns:
        Sorted list of agent names.
    """
    return sorted(_REGISTRY.keys())
