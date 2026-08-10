"""Export/import a session's ``/pvc`` data volume as a portable tarball.

The whole durable state of a paude session lives in one named volume mounted at
``/pvc`` (workspace + agent state). ``paude backup`` captures it; a future
``paude restore`` will repopulate it. Both work for **podman and docker** and
for **local and SSH-remote** sessions by reusing the transport-aware copy
helpers in :mod:`paude.backends.podman.file_copy`.

Why a helper container rather than ``podman volume export``: ``volume export``
is podman-only (docker has no equivalent), and streaming a tar through
``ContainerEngine.run`` would corrupt it because the transport runs in text
mode. Instead a throwaway container tars the volume to a file *on the engine
host* (hashing it in the same pass so the multi-GB archive isn't read twice),
and :func:`copy_from_container` pulls the bytes back (staging on the remote host
and using ``transport.copy_from_host`` for SSH).

Restore (future, ``import_volume``) is the mirror image: create the volume,
push the tar in with :func:`copy_to_container`, and ``tar -xzf`` it into
``/pvc`` from inside a helper container (on docker, ``chown paude /pvc`` first,
matching ``SessionSetup.fix_volume_permissions``).
"""

from __future__ import annotations

import secrets
import shlex
from collections.abc import Iterator
from contextlib import contextmanager

from paude.backends.podman.file_copy import copy_from_container
from paude.container.engine import ContainerEngine

# Where the helper container writes the archive inside its own filesystem.
CONTAINER_ARCHIVE_PATH = "/tmp/pvc.tar.gz"  # noqa: S108


class VolumeArchiver:
    """Capture a named volume's contents to a local tarball."""

    def __init__(self, engine: ContainerEngine) -> None:
        self._engine = engine

    @contextmanager
    def _helper_container(self) -> Iterator[str]:
        """Yield a unique helper-container name, force-removing it on exit."""
        helper = f"paude-backup-{secrets.token_hex(6)}"
        try:
            yield helper
        finally:
            self._engine.run("rm", "-f", helper, check=False)

    def volume_size_bytes(self, volume: str, image: str) -> int | None:
        """Return the on-disk size of a named volume in bytes, or None.

        Runs ``du -sb`` in a throwaway container mounting the volume read-only.
        This is the *uncompressed* size — a conservative upper bound for the
        resulting archive, suitable for a free-space preflight. Returns None if
        the size can't be determined (e.g. ``du`` unavailable in the image).
        """
        with self._helper_container() as helper:
            result = self._engine.run(
                "run",
                "--name",
                helper,
                "--entrypoint",
                "du",
                "-v",
                f"{volume}:/pvc:ro",
                image,
                "-sb",
                "/pvc",
                check=False,
            )
        if result.returncode != 0:
            return None
        first = result.stdout.strip().split()
        return int(first[0]) if first and first[0].isdigit() else None

    def export_volume(
        self,
        volume: str,
        image: str,
        local_tar_path: str,
        *,
        exclude: list[str] | None = None,
    ) -> str:
        """Export ``volume`` to ``local_tar_path`` as a gzipped tar.

        Runs a throwaway container that mounts the volume read-only, tars its
        contents, and hashes the archive in the same pass (so the multi-GB file
        isn't read a second time), then pulls the archive to the local machine.
        The helper is always removed, even on failure.

        Args:
            volume: Named volume to export (mounted at ``/pvc``).
            image: Container image to run the helper with (the session's own
                image, already present on the engine host — must provide ``tar``).
            local_tar_path: Destination path on the local machine.
            exclude: HOME-relative ``tar --exclude`` globs (e.g. credential
                files) to omit from the archive.

        Returns:
            The archive's SHA-256 hex digest (computed inside the container).
        """
        with self._helper_container() as helper:
            # `--entrypoint sh` overrides any entrypoint baked into the session
            # image; the container tars the read-only volume into its own
            # writable layer and prints the archive's digest to stdout.
            result = self._engine.run(
                "run",
                "--name",
                helper,
                "--entrypoint",
                "sh",
                "-v",
                f"{volume}:/pvc:ro",
                image,
                "-c",
                _tar_and_hash_script(exclude or []),
            )
            copy_from_container(
                self._engine, helper, CONTAINER_ARCHIVE_PATH, local_tar_path
            )
        return result.stdout.strip()


def _tar_and_hash_script(exclude: list[str]) -> str:
    """Build a ``sh -c`` script that tars ``/pvc`` then prints the sha256 hex."""
    archive = shlex.quote(CONTAINER_ARCHIVE_PATH)
    parts = ["tar", "-czf", archive]
    for pattern in exclude:
        parts.extend(["--exclude", shlex.quote(pattern)])
    parts.extend(["-C", "/pvc", "."])
    return f"{' '.join(parts)} && sha256sum {archive} | cut -d' ' -f1"
