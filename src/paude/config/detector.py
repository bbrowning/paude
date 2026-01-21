"""Configuration file detection for paude."""

from __future__ import annotations

from pathlib import Path


def detect_config(workspace: Path) -> Path | None:
    """Detect configuration file in the workspace.

    Priority order:
    1. .devcontainer/devcontainer.json
    2. .devcontainer.json
    3. paude.json

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Path to the config file if found, None otherwise.
    """
    candidates = [
        workspace / ".devcontainer" / "devcontainer.json",
        workspace / ".devcontainer.json",
        workspace / "paude.json",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None
