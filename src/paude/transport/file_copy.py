"""Filesystem copy helpers shared by transport implementations."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


def copies_directory_contents(path: str) -> bool:
    """Return whether a copy path uses trailing ``/.`` semantics."""
    return path.endswith("/.")


def without_contents_suffix(path: str) -> str:
    """Remove a trailing ``/.`` while retaining a usable root path."""
    stripped = path[:-2]
    return stripped or "/"


def copy_path(source: Path, destination: Path, *, contents: bool = False) -> None:
    """Copy a path without following source or destination symlinks."""
    if not os.path.lexists(source):
        raise FileNotFoundError(str(source))

    if contents:
        if source.is_symlink() or not source.is_dir():
            raise ValueError(f"Copy source is not a directory: {source}")
        _prepare_directory(destination)
        for child in source.iterdir():
            _copy_entry(child, destination / child.name)
        return

    target = (
        destination / source.name if _is_real_directory(destination) else destination
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    _copy_entry(source, target)


def _copy_entry(source: Path, destination: Path) -> None:
    """Copy one filesystem entry, recursively, using lstat semantics."""
    source_mode = source.lstat().st_mode
    if stat.S_ISLNK(source_mode):
        _remove_non_directory(destination)
        os.symlink(os.readlink(source), destination)
        return

    if stat.S_ISDIR(source_mode):
        _prepare_directory(destination)
        for child in source.iterdir():
            _copy_entry(child, destination / child.name)
        shutil.copystat(source, destination, follow_symlinks=False)
        return

    if not stat.S_ISREG(source_mode):
        raise ValueError(f"Unsupported copy source type: {source}")

    _remove_non_directory(destination)
    shutil.copy2(source, destination, follow_symlinks=False)


def _prepare_directory(path: Path) -> None:
    """Create a real directory, replacing a file or symlink if necessary."""
    if os.path.lexists(path):
        if _is_real_directory(path):
            return
        path.unlink()
    path.mkdir(parents=True)


def _remove_non_directory(path: Path) -> None:
    """Remove an existing file or symlink without traversing directories."""
    if not os.path.lexists(path):
        return
    if _is_real_directory(path):
        raise IsADirectoryError(str(path))
    path.unlink()


def _is_real_directory(path: Path) -> bool:
    """Return whether a path is a directory rather than a link to one."""
    return not path.is_symlink() and path.is_dir()
