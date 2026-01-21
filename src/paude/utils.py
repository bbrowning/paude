"""Utility functions for paude."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


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


class RequirementError(Exception):
    """Error when a required tool is missing."""


def check_requirements() -> None:
    """Verify podman is installed.

    Raises:
        RequirementError: If podman is not found.
    """
    if not shutil.which("podman"):
        raise RequirementError(
            "podman is required but not found.\n"
            "Install from: https://podman.io/getting-started/installation"
        )


def check_git_safety(workspace: Path) -> None:
    """Warn if workspace has no git repo or remotes.

    Args:
        workspace: Path to the workspace directory.
    """
    git_dir = workspace / ".git"
    if not git_dir.exists():
        print("Warning: No git repository in workspace.", file=sys.stderr)
        msg = "  Without git, deleted/modified files cannot be recovered."
        print(msg, file=sys.stderr)
        print(
            "  Consider: git init && git add -A && git commit -m 'Initial'",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        return

    # Check for remotes
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "remote"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and not result.stdout.strip():
            print("Warning: Git repository has no remotes.", file=sys.stderr)
            msg = "  Without a remote, your work has no off-machine backup."
            print(msg, file=sys.stderr)
            print(
                "  Consider: git remote add origin <url> && git push",
                file=sys.stderr,
            )
            print("", file=sys.stderr)
    except subprocess.SubprocessError:
        # Git command failed, just ignore
        pass
