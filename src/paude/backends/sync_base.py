"""Base configuration synchronization for containers.

Shared orchestration logic for copying host config files into
/credentials/ so the entrypoint's setup_credentials() processes them.
Subclasses provide transport-specific implementations (podman cp, oc cp/rsync).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from paude.backends.shared import config_file_basename

if TYPE_CHECKING:
    from paude.agents.base import Agent

CONFIG_PATH = "/credentials"


class BaseConfigSyncer(ABC):
    """Base class for syncing host configuration into containers.

    Provides shared decision logic for which files to sync.
    Subclasses implement transport-specific copy and exec methods.

    Subclasses store ``_target`` (container/pod name) as instance state
    set at the start of each public sync call. This is not thread-safe;
    each syncer instance should be used from a single thread at a time.

    Note: ``_copy_dir`` receives ``excludes`` from the shared orchestration.
    Transports that lack native exclude support (e.g. podman cp) should
    filter locally before copying.
    """

    # -- abstract transport methods ----------------------------------------

    @abstractmethod
    def _copy_file(self, local_path: str, container_path: str, *, context: str) -> bool:
        """Copy a single file into the container. Returns True on success."""

    @abstractmethod
    def _copy_dir(
        self,
        local_dir: str,
        container_path: str,
        *,
        excludes: list[str] | None = None,
        context: str,
    ) -> bool:
        """Copy directory contents into the container. Returns True on success."""

    @abstractmethod
    def _rewrite_plugin_paths(self, agent_path: str, agent: Agent, home: Path) -> None:
        """Rewrite absolute host paths in plugin metadata files."""

    # -- shared orchestration ----------------------------------------------

    def _sync_config_files(self, agent_name: str) -> None:
        """Sync config files common to all backends.

        Copies agent config directory/files, cursor auth, gitconfig,
        and rewrites plugin paths. Subclasses call this from their
        public sync methods, wrapping with backend-specific prepare
        and finalize steps.
        """
        from paude.agents import get_agent

        agent = get_agent(agent_name)
        home = Path.home()
        agent_path = f"{CONFIG_PATH}/{agent_name}"

        config_synced = self._sync_agent_config(agent_path, agent, home)
        self._sync_agent_config_file(agent_path, agent, home)
        if agent_name == "cursor":
            self._sync_cursor_auth(home)
        self._sync_gitconfig(home)
        self._sync_global_gitignore(home)
        if config_synced:
            self._rewrite_plugin_paths(agent_path, agent, home)

    # -- shared step implementations ---------------------------------------

    def _sync_agent_config(self, agent_path: str, agent: Agent, home: Path) -> bool:
        """Sync agent config directory. Returns True on success."""
        config_dir = home / agent.config.config_dir_name
        if not config_dir.is_dir():
            return True

        if agent.config.config_sync_files_only:
            for filename in agent.config.config_sync_files_only:
                filepath = config_dir / filename
                if filepath.exists():
                    self._copy_file(
                        str(filepath),
                        f"{agent_path}/{filename}",
                        context=f"copy agent config file {filename}",
                    )
            return True

        return self._copy_dir(
            str(config_dir),
            agent_path,
            excludes=list(agent.config.config_excludes),
            context="copy agent config directory",
        )

    def _sync_agent_config_file(
        self, agent_path: str, agent: Agent, home: Path
    ) -> None:
        """Sync agent config file (e.g., .claude.json)."""
        if not agent.config.config_file_name:
            return
        config_file = home / agent.config.config_file_name
        if config_file.is_file():
            basename = config_file_basename(agent.config.config_file_name)
            self._copy_file(
                str(config_file),
                f"{agent_path}/{basename}",
                context=f"copy agent config file {agent.config.config_file_name}",
            )

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
