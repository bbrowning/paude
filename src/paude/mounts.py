"""Volume mount builder for paude containers."""

from __future__ import annotations

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


def build_mounts(workspace: Path, home: Path) -> list[str]:
    """Build the list of volume mount arguments for podman.

    Mounts (in order):
    1. Workspace at same path (rw)
    2. gcloud config (ro, if exists)
    3. Claude seed directory (ro, if exists)
    4. Plugins at original host path (ro, if exists)
    5. gitconfig (ro, if exists)
    6. claude.json seed (ro, if exists)

    Args:
        workspace: Path to the workspace directory.
        home: Path to the user's home directory.

    Returns:
        List of mount argument strings (e.g., ["-v", "/path:/path:rw", ...]).
    """
    mounts: list[str] = []

    # 1. Workspace mount (always present)
    resolved_workspace = resolve_path(workspace)
    if resolved_workspace:
        mounts.extend(["-v", f"{resolved_workspace}:{resolved_workspace}:rw"])
    else:
        # Workspace should always exist, but handle gracefully
        mounts.extend(["-v", f"{workspace}:{workspace}:rw"])

    # 2. gcloud config (ro)
    gcloud_dir = home / ".config" / "gcloud"
    resolved_gcloud = resolve_path(gcloud_dir)
    if resolved_gcloud and resolved_gcloud.is_dir():
        mounts.extend(["-v", f"{resolved_gcloud}:/home/paude/.config/gcloud:ro"])

    # 3. Claude seed directory (ro)
    claude_dir = home / ".claude"
    resolved_claude = resolve_path(claude_dir)
    if resolved_claude and resolved_claude.is_dir():
        mounts.extend(["-v", f"{resolved_claude}:/tmp/claude.seed:ro"])

        # 4. Plugins at original host path (ro)
        plugins_dir = resolved_claude / "plugins"
        if plugins_dir.is_dir():
            mounts.extend(["-v", f"{plugins_dir}:{plugins_dir}:ro"])

    # 5. gitconfig (ro)
    gitconfig = home / ".gitconfig"
    resolved_gitconfig = resolve_path(gitconfig)
    if resolved_gitconfig and resolved_gitconfig.is_file():
        mounts.extend(["-v", f"{resolved_gitconfig}:/home/paude/.gitconfig:ro"])

    # 6. claude.json seed (ro)
    claude_json = home / ".claude.json"
    resolved_claude_json = resolve_path(claude_json)
    if resolved_claude_json and resolved_claude_json.is_file():
        mounts.extend(["-v", f"{resolved_claude_json}:/tmp/claude.json.seed:ro"])

    return mounts
