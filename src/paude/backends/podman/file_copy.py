"""Copy helpers for local and SSH-hosted container sessions."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from paude.container.engine import ContainerEngine
from paude.transport.file_copy import copies_directory_contents

COPY_TEMP_PREFIX = "/tmp/paude-copy-"  # noqa: S108


def copy_to_container(
    engine: ContainerEngine,
    container_name: str,
    local_path: str,
    remote_path: str,
) -> None:
    """Copy a local path into a container running on the engine host."""
    if not engine.is_remote:
        engine.run("cp", local_path, f"{container_name}:{remote_path}")
        return

    staging = _create_staging_directory(engine)
    copy_contents = copies_directory_contents(local_path)
    staged_path = (
        f"{staging}/." if copy_contents else f"{staging}/{_path_name(local_path)}"
    )
    try:
        engine.transport.copy_to_host(local_path, staged_path)
        engine.run("cp", staged_path, f"{container_name}:{remote_path}")
    finally:
        _remove_staging_directory(engine, staging)


def copy_from_container(
    engine: ContainerEngine,
    container_name: str,
    remote_path: str,
    local_path: str,
) -> None:
    """Copy a container path to a local path on the client machine."""
    if not engine.is_remote:
        engine.run("cp", f"{container_name}:{remote_path}", local_path)
        return

    staging = _create_staging_directory(engine)
    copy_contents = copies_directory_contents(remote_path)
    staged_path = (
        f"{staging}/." if copy_contents else f"{staging}/{_path_name(remote_path)}"
    )
    try:
        # Copying into an existing directory retains the source basename for
        # both files and directories, matching normal container cp behavior.
        engine.run("cp", f"{container_name}:{remote_path}", staging)
        engine.transport.copy_from_host(staged_path, local_path)
    finally:
        _remove_staging_directory(engine, staging)


def _create_staging_directory(engine: ContainerEngine) -> str:
    """Create a temporary directory on the container engine host."""
    result = engine.transport.run(["mktemp", "-d", f"{COPY_TEMP_PREFIX}XXXXXX"])
    staging = result.stdout.strip()
    if not staging or not staging.startswith(COPY_TEMP_PREFIX):
        raise RuntimeError("Remote copy did not return a valid staging directory")
    return staging


def _remove_staging_directory(engine: ContainerEngine, staging: str) -> None:
    """Remove a temporary copy directory, ignoring cleanup failures."""
    engine.transport.run(["rm", "-rf", staging], check=False)


def _path_name(path: str) -> str:
    """Return a basename suitable for staging a path."""
    name = PurePosixPath(path.rstrip("/")).name
    if name in {"", ".", ".."}:
        name = Path(path).resolve().name
    if not name:
        raise ValueError(f"Invalid copy path: {path}")
    return name
