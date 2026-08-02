"""State migration helpers used before replacing a session container."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from paude.constants import CONTAINER_HOME

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.container.runner import ContainerRunner


_MIGRATE_SCRIPT = r"""
set -e
mode=dirs
for relative in "$@"; do
    if [ "$relative" = "--files" ]; then mode=files; continue; fi
    source_path="$HOME/$relative"
    target_path="/pvc/$relative"
    if [ -L "$source_path" ]; then continue; fi
    if [ "$mode" = "dirs" ] && [ -d "$source_path" ]; then
        mkdir -p "$target_path"
        cp -dR --preserve=mode,timestamps "$source_path/." "$target_path/"
        chmod -R g+rwX "$target_path" 2>/dev/null || true
        chcon -R --reference=/pvc "$target_path" 2>/dev/null || true
    elif [ "$mode" = "files" ] && [ -f "$source_path" ]; then
        mkdir -p "$(dirname "$target_path")"
        cp -f --preserve=mode,timestamps "$source_path" "$target_path"
        chmod g+rw "$target_path" 2>/dev/null || true
        chcon --reference=/pvc "$target_path" 2>/dev/null || true
    fi
done
"""


def persistent_state_paths(
    composition: AgentComposition,
) -> tuple[list[str], list[str]]:
    """Return deduplicated HOME-relative state directories and files."""
    directories = list(
        dict.fromkeys(
            [
                *(
                    directory
                    for agent in composition.agents
                    for directory in agent.config.persistent_dir_names
                ),
                ".dolt",
            ]
        )
    )
    files = list(
        dict.fromkeys(
            [
                *(
                    agent.config.config_file_name
                    for agent in composition.agents
                    if agent.config.config_file_name
                ),
                ".gitconfig",
            ]
        )
    )
    _validate_relative_paths([*directories, *files])
    return directories, files


def migrate_legacy_state(
    runner: ContainerRunner,
    container_name: str,
    composition: AgentComposition,
) -> None:
    """Copy legacy writable-layer state into the mounted session volume."""
    directories, files = persistent_state_paths(composition)
    was_running = runner.container_running(container_name)
    if was_running:
        runner.stop_container_graceful(container_name)
    runner.start_container(container_name)
    try:
        runner.exec_in_container(
            container_name,
            [
                "env",
                f"HOME={CONTAINER_HOME}",
                "bash",
                "-c",
                _MIGRATE_SCRIPT,
                "paude-state-migration",
                *directories,
                "--files",
                *files,
            ],
        )
    finally:
        if not was_running:
            runner.stop_container_graceful(container_name)


def _validate_relative_paths(paths: list[str]) -> None:
    """Reject persistence declarations that could escape HOME or the PVC."""
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or not raw_path.strip():
            raise ValueError(f"Unsafe persistent state path: {raw_path!r}")
