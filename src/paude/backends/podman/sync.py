"""Configuration synchronization for Podman containers.

Uses podman cp/exec to copy host config files into /credentials/
so the entrypoint's setup_credentials() processes them.
"""

from __future__ import annotations

import sys

from paude.backends.sync_base import CONFIG_PATH, BaseConfigSyncer
from paude.container.engine import ContainerEngine


class ConfigSyncer(BaseConfigSyncer):
    """Podman-specific config syncer using podman cp/exec."""

    def __init__(self, engine: ContainerEngine) -> None:
        self._engine = engine
        self._target = ""

    def sync(self, cname: str, agent_name: str) -> None:
        """Run a full config sync to /credentials/ in the container.

        Skipped for SSH remotes which use bind mounts instead.
        """
        if self._engine.is_remote:
            return

        self._target = cname

        self._prepare_directory()
        self._sync_config_files(agent_name)
        self._finalize()

    # -- transport implementation ------------------------------------------

    def _run_step(self, *args: str, context: str) -> bool:
        result = self._engine.run(*args, check=False)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = f": {stderr}" if stderr else ""
            print(
                f"Warning: podman config sync step failed ({context}){detail}",
                file=sys.stderr,
            )
            return False
        return True

    def _copy_file(self, local_path: str, container_path: str, *, context: str) -> bool:
        return self._run_step(
            "cp",
            local_path,
            f"{self._target}:{container_path}",
            context=context,
        )

    def _prepare_directory(self) -> None:
        t = self._target
        self._run_step(
            "exec",
            "--user",
            "root",
            t,
            "mkdir",
            "-p",
            CONFIG_PATH,
            context="create credentials directory",
        )
        self._run_step(
            "exec",
            "--user",
            "root",
            t,
            "chown",
            "paude:0",
            CONFIG_PATH,
            context="set credentials directory ownership",
        )

    def _finalize(self) -> None:
        t = self._target
        self._run_step(
            "exec",
            "--user",
            "root",
            t,
            "chown",
            "-R",
            "paude:0",
            CONFIG_PATH,
            context="set credentials ownership recursively",
        )
        self._run_step(
            "exec",
            "--user",
            "root",
            t,
            "touch",
            f"{CONFIG_PATH}/.ready",
            context="create credentials ready marker",
        )
