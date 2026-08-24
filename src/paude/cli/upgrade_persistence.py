"""State migration helpers used before replacing a session container."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from paude.cli.helpers import called_process_stderr
from paude.constants import CONTAINER_HOME
from paude.container.runner import echo_captured_stderr

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.container.runner import ContainerRunner


# Per-path writes are non-fatal: one un-writable path (e.g. a /pvc whose UID
# drifted from the runtime user) must not abort the whole upgrade. Each mkdir/cp
# is guarded and warns on stderr (which the caller surfaces); cp's own error is
# left visible so the specific failing path is diagnosable. This mirrors the
# runtime entrypoint's hardened persist_config_dir. set -e still guards the rest.
_MIGRATE_SCRIPT = r"""
set -e
warn() { echo "migrate: could not fully migrate $1" >&2; }
mode=dirs
for relative in "$@"; do
    if [ "$relative" = "--files" ]; then mode=files; continue; fi
    src="$HOME/$relative"
    dst="/pvc/$relative"
    if [ -L "$src" ]; then continue; fi
    if [ "$mode" = "dirs" ] && [ -d "$src" ]; then
        mkdir -p "$dst" 2>/dev/null || { warn "$relative"; continue; }
        cp -dR --preserve=mode,timestamps "$src/." "$dst/" || warn "$relative"
        chmod -R g+rwX "$dst" 2>/dev/null || true
        chcon -R --reference=/pvc "$dst" 2>/dev/null || true
    elif [ "$mode" = "files" ] && [ -f "$src" ]; then
        mkdir -p "$(dirname "$dst")" 2>/dev/null || { warn "$relative"; continue; }
        cp -f --preserve=mode,timestamps "$src" "$dst" || warn "$relative"
        chmod g+rw "$dst" 2>/dev/null || true
        chcon --reference=/pvc "$dst" 2>/dev/null || true
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


def credential_exclude_globs(composition: AgentComposition) -> list[str]:
    """Return HOME-relative credential files to exclude from a backup.

    Sourced from each installed agent's ``credential_file_names`` so the set
    stays agent-driven. ``paude backup`` passes these to ``tar --exclude`` so a
    bundle never persists a live token to disk (see KNOWN_ISSUES.md for the
    underlying Gemini/Cursor leak these guard against).
    """
    globs = list(
        dict.fromkeys(
            name
            for agent in composition.agents
            for name in agent.config.credential_file_names
        )
    )
    _validate_relative_paths(globs)
    return globs


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
    try:
        runner.start_container(container_name)
    except subprocess.CalledProcessError as e:
        # Salvage is best-effort: an old container that can't even start
        # (e.g. a stale network reference from a previous bug) has nothing
        # new to offer beyond what's already persisted to the session
        # volume, so skip it with a warning instead of blocking the upgrade.
        detail = called_process_stderr(e) or str(e)
        print(
            f"migrate: could not start {container_name} to migrate legacy "
            f"state ({detail}); skipping salvage. State already persisted "
            "to the session volume is unaffected.",
            file=sys.stderr,
        )
        return
    try:
        # Run as the old container's default user (not root): the copy is a
        # best-effort salvage into the volume that user already owns. Anything it
        # can't read or write — e.g. a read-only host-mounted ~/.gitconfig owned
        # by root on a remote session — is skipped with a warning by the hardened
        # script instead of aborting the upgrade. Volume ownership is NOT
        # reconciled here: the old container may run as a pre-pin UID that owns
        # the volume, and chowning it to the pinned user would only take write
        # access away. Final ownership is reconciled on the recreated container
        # (SessionSetup.fix_volume_permissions), which runs as the pinned user.
        result = runner.exec_in_container(
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
        # Surface any non-fatal per-path warnings the hardened script emitted.
        echo_captured_stderr(result)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"migrating persistent agent state failed: {e.stderr or e.stdout or e}"
        ) from e
    finally:
        if not was_running:
            runner.stop_container_graceful(container_name)


def _validate_relative_paths(paths: list[str]) -> None:
    """Reject persistence declarations that could escape HOME or the PVC."""
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or not raw_path.strip():
            raise ValueError(f"Unsafe persistent state path: {raw_path!r}")
