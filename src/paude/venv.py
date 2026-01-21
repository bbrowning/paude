"""Python virtual environment detection for paude."""

from __future__ import annotations

from pathlib import Path

COMMON_VENV_NAMES = [".venv", "venv", ".virtualenv", "env", ".env"]


def is_venv(path: Path) -> bool:
    """Check if a directory is a Python virtual environment.

    A directory is considered a venv if it contains a pyvenv.cfg file.

    Args:
        path: Path to check.

    Returns:
        True if the path is a venv directory.
    """
    if not path.is_dir():
        return False
    return (path / "pyvenv.cfg").is_file()


def find_venvs(workspace: Path) -> list[Path]:
    """Find Python virtual environment directories in a workspace.

    Only checks common venv names at the top level of the workspace
    (non-recursive). A directory is identified as a venv if it contains
    a pyvenv.cfg file.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        List of paths to detected venv directories.
    """
    venvs = []
    for name in COMMON_VENV_NAMES:
        candidate = workspace / name
        if is_venv(candidate):
            venvs.append(candidate)
    return venvs
