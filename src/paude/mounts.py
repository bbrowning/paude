"""Volume mount builder for paude containers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from paude.constants import CONTAINER_HOME

if TYPE_CHECKING:
    from paude.agents.base import Agent


def resolve_path(path: Path) -> Path | None:
    """Resolve symlinks to physical path.

    Args:
        path: Path to resolve.

    Returns:
        Resolved path, or None if path doesn't exist.
    """
    try:
        if path.exists():
            return path.resolve()
    except OSError:
        pass
    return None


def build_mounts(home: Path, agent: Agent | None = None) -> list[str]:
    """Build the list of volume mount arguments for podman.

    Note: Workspace is NOT mounted here - it uses a named volume at /pvc/workspace.
    Users sync code via git remote (paude remote add + git push/pull).

    Note: gcloud ADC credentials are injected via Podman secrets, not bind mounts.

    Args:
        home: Path to the user's home directory.
        agent: Agent instance for agent-specific mounts. If None, uses Claude defaults.

    Returns:
        List of mount argument strings (e.g., ["-v", "/path:/path:rw", ...]).
    """
    mounts: list[str] = []

    # Agent-specific config mounts
    if agent is not None:
        mounts.extend(agent.host_config_mounts(home))
    else:
        # Backward compat: Claude defaults when no agent provided
        from paude.agents import get_agent

        claude = get_agent("claude")
        mounts.extend(claude.host_config_mounts(home))

    # gitconfig (ro) - shared across all agents
    gitconfig = home / ".gitconfig"
    resolved_gitconfig = resolve_path(gitconfig)
    if resolved_gitconfig and resolved_gitconfig.is_file():
        mounts.extend(["-v", f"{resolved_gitconfig}:{CONTAINER_HOME}/.gitconfig:ro"])

    return mounts
