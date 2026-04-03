"""Base configuration synchronization for containers.

Shared orchestration logic for copying host config files into
/credentials/ so the entrypoint's setup_credentials() processes them.
Subclasses provide transport-specific implementations (podman cp, oc cp/rsync).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

CONFIG_PATH = "/credentials"


class BaseConfigSyncer(ABC):
    """Base class for syncing host configuration into containers.

    Provides shared decision logic for which files to sync.
    Subclasses implement transport-specific copy and exec methods.

    Subclasses store ``_target`` (container/pod name) as instance state
    set at the start of each public sync call. This is not thread-safe;
    each syncer instance should be used from a single thread at a time.
    """

    # -- abstract transport methods ----------------------------------------

    @abstractmethod
    def _copy_file(self, local_path: str, container_path: str, *, context: str) -> bool:
        """Copy a single file into the container. Returns True on success."""

    # -- shared orchestration ----------------------------------------------

    def _sync_config_files(self, agent_name: str) -> None:
        """Sync config files common to all backends.

        Copies cursor auth, gitconfig, and global gitignore.
        Subclasses call this from their public sync methods, wrapping
        with backend-specific prepare and finalize steps.
        """
        home = Path.home()

        if agent_name == "cursor":
            self._sync_cursor_auth(home)
        self._sync_gitconfig(home)
        self._sync_global_gitignore(home)

    # -- shared step implementations ---------------------------------------

    def _sync_cursor_auth(self, home: Path) -> None:
        """Sync Cursor auth.json from ~/.config/cursor/."""
        auth_json = home / ".config" / "cursor" / "auth.json"
        if auth_json.is_file():
            self._copy_file(
                str(auth_json),
                f"{CONFIG_PATH}/cursor-auth.json",
                context="copy cursor auth.json",
            )

    def _sync_gitconfig(self, home: Path) -> None:
        """Sync ~/.gitconfig."""
        gitconfig = home / ".gitconfig"
        if gitconfig.is_file():
            self._copy_file(
                str(gitconfig),
                f"{CONFIG_PATH}/gitconfig",
                context="copy gitconfig",
            )

    def _sync_global_gitignore(self, home: Path) -> None:
        """Sync ~/.config/git/ignore (global gitignore)."""
        global_gitignore = home / ".config" / "git" / "ignore"
        if global_gitignore.is_file():
            self._copy_file(
                str(global_gitignore),
                f"{CONFIG_PATH}/gitignore-global",
                context="copy global gitignore",
            )
