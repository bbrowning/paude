"""Volume mount builder for paude containers."""

from __future__ import annotations

import sys
from pathlib import Path

from paude.config import VenvMode


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


def build_venv_mounts(workspace: Path, venv_mode: VenvMode) -> list[str]:
    """Build tmpfs mounts to shadow Python venv directories.

    These mounts should be added AFTER the workspace mount so they overlay
    the venv directories with empty tmpfs mounts. This allows the container
    to create its own venv without conflicting with the host venv.

    Args:
        workspace: Path to the workspace directory.
        venv_mode: "auto" to detect venvs, "none" to disable,
                   or list of directory names to shadow.

    Returns:
        List of mount arguments for podman (["--mount", "type=tmpfs,...", ...]).
    """
    from paude.venv import find_venvs, is_venv

    if venv_mode == "none":
        return []

    if venv_mode == "auto":
        venvs = find_venvs(workspace)
    else:
        venvs = []
        for name in venv_mode:
            candidate = workspace / name
            if candidate.exists() and is_venv(candidate):
                venvs.append(candidate)

    if not venvs:
        return []

    venv_names = [v.name for v in venvs]
    print(f"Shadowing venv: {', '.join(venv_names)}", file=sys.stderr)

    mounts: list[str] = []
    for venv_path in venvs:
        resolved = resolve_path(venv_path)
        if resolved:
            mount_spec = (
                f"type=tmpfs,destination={resolved},"
                "notmpcopyup,tmpfs-mode=1777"
            )
            mounts.extend(["--mount", mount_spec])

    return mounts


def get_venv_paths(workspace: Path, venv_mode: VenvMode) -> list[Path]:
    """Get list of venv paths that will be shadowed.

    Args:
        workspace: Path to the workspace directory.
        venv_mode: "auto" to detect venvs, "none" to disable,
                   or list of directory names to shadow.

    Returns:
        List of resolved venv paths.
    """
    from paude.venv import find_venvs, is_venv

    if venv_mode == "none":
        return []

    if venv_mode == "auto":
        venvs = find_venvs(workspace)
    else:
        venvs = []
        for name in venv_mode:
            candidate = workspace / name
            if candidate.exists() and is_venv(candidate):
                venvs.append(candidate)

    result = []
    for venv_path in venvs:
        resolved = resolve_path(venv_path)
        if resolved:
            result.append(resolved)

    return result
