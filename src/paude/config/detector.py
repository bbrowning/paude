"""Configuration file detection for paude."""

from __future__ import annotations

import sys
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
        (workspace / ".devcontainer" / "devcontainer.json", "devcontainer"),
        (workspace / ".devcontainer.json", "devcontainer"),
        (workspace / "paude.json", "paude"),
    ]

    for candidate, config_type in candidates:
        if candidate.exists():
            print(f"Detected {config_type} config: {candidate}", file=sys.stderr)
            return candidate

    return None
