"""Configuration file detection for paude."""

from __future__ import annotations

import sys
from pathlib import Path


def detect_config(workspace: Path) -> Path | None:
    """Detect configuration file in the workspace.

    The only project configuration file recognized by paude is ``paude.json``.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        Path to the config file if found, None otherwise.
    """
    candidate = workspace / "paude.json"
    if candidate.exists():
        print(f"Detected paude config: {candidate}", file=sys.stderr)
        return candidate

    return None
