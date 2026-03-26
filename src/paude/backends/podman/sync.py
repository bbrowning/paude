"""Configuration synchronization for Podman containers.

Uses podman cp/exec to copy host config files into /credentials/
so the entrypoint's setup_credentials() processes them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from paude.backends.sync_base import CONFIG_PATH, BaseConfigSyncer
from paude.constants import CONTAINER_HOME
from paude.container.engine import ContainerEngine

if TYPE_CHECKING:
    from paude.agents.base import Agent


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
        agent_path = f"{CONFIG_PATH}/{agent_name}"

        self._prepare_directory(agent_path)
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

    def _copy_dir(
        self,
        local_dir: str,
        container_path: str,
        *,
        excludes: list[str] | None = None,
        context: str,
    ) -> bool:
        if excludes:
            import shutil
            import tempfile

            patterns = {e.strip("/") for e in excludes}

            def _ignore(_dir: str, entries: list[str]) -> set[str]:
                return {e for e in entries if e in patterns}

            with tempfile.TemporaryDirectory() as tmp:
                filtered = str(Path(tmp) / "filtered")
                shutil.copytree(local_dir, filtered, ignore=_ignore)
                return self._run_step(
                    "cp",
                    f"{filtered}/.",
                    f"{self._target}:{container_path}",
                    context=context,
                )
        return self._run_step(
            "cp",
            f"{local_dir}/.",
            f"{self._target}:{container_path}",
            context=context,
        )

    def _prepare_directory(self, agent_path: str) -> None:
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
        self._run_step(
            "exec",
            "--user",
            "root",
            t,
            "mkdir",
            "-p",
            agent_path,
            context="create agent credentials directory",
        )

    def _rewrite_plugin_paths(self, agent_path: str, agent: Agent, home: Path) -> None:
        host_home = str(home)
        container_home = CONTAINER_HOME
        if host_home == container_home:
            return

        plugin_files = [
            f"{agent_path}/plugins/installed_plugins.json",
            f"{agent_path}/plugins/known_marketplaces.json",
        ]
        t = self._target
        for plugin_file in plugin_files:
            exists = self._run_step(
                "exec",
                "--user",
                "root",
                t,
                "test",
                "-f",
                plugin_file,
                context=f"check plugin file exists: {plugin_file}",
            )
            if not exists:
                continue

            python_script = (
                "from pathlib import Path\n"
                "p = Path(__import__('sys').argv[1])\n"
                "old = __import__('sys').argv[2]\n"
                "new = __import__('sys').argv[3]\n"
                "p.write_text(p.read_text().replace(old, new))\n"
            )
            self._run_step(
                "exec",
                "--user",
                "root",
                t,
                "python3",
                "-c",
                python_script,
                plugin_file,
                host_home,
                container_home,
                context=f"rewrite plugin home paths in {plugin_file}",
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
