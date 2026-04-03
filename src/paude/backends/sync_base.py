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

        Copies gitconfig to the container.
        Subclasses call this from their public sync methods, wrapping
        with backend-specific prepare and finalize steps.
        """
        home = Path.home()
        self._sync_gitconfig(home)

    # -- shared step implementations ---------------------------------------

    def _sync_gitconfig(self, home: Path) -> None:
        """Sync ~/.gitconfig."""
        gitconfig = home / ".gitconfig"
        if gitconfig.is_file():
            self._copy_file(
                str(gitconfig),
                f"{CONFIG_PATH}/gitconfig",
                context="copy gitconfig",
            )
