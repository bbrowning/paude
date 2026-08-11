"""Export/import a session's ``/pvc`` data volume as a portable tarball.

The whole durable state of a paude session lives in one named volume mounted at
``/pvc`` (workspace + agent state). ``paude backup`` captures it; a future
``paude restore`` will repopulate it. Both work for **podman and docker** and
for **local and SSH-remote** sessions.

Why a helper container rather than ``podman volume export``: ``volume export``
is podman-only (docker has no equivalent). Instead a throwaway container tars the
read-only volume to its **stdout**, and that byte stream is piped straight to the
client (over SSH for remote sessions) where it is written to the bundle and
hashed as it flows through. Streaming this way means the multi-GB archive is
never materialized on the engine host — critical for remote hosts whose ``/tmp``
or container storage is far smaller than the volume — and lets the client report
live progress. The stream is handled in **binary** mode via
:meth:`ContainerEngine.stream_run` (not the text-mode ``run``, which would
corrupt the gzip bytes).

Restore (future, ``import_volume``) is the mirror image: create the volume,
push the tar in with :func:`paude.backends.podman.file_copy.copy_to_container`,
and ``tar -xzf`` it into ``/pvc`` from inside a helper container, reconciling
ownership to the pinned runtime user afterward (see
``SessionSetup.fix_volume_permissions`` /
``ContainerRunner.reconcile_volume_ownership``).
"""

from __future__ import annotations

import hashlib
import secrets
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from paude.container.engine import ContainerEngine

# Read the archive stream one MiB at a time.
_CHUNK_SIZE = 1024 * 1024


class VolumeArchiver:
    """Capture a named volume's contents to a local tarball."""

    def __init__(self, engine: ContainerEngine) -> None:
        self._engine = engine

    @contextmanager
    def _helper_container(self) -> Iterator[str]:
        """Yield a unique helper-container name, force-removing it on exit.

        The helper is removed even if the client interrupts a stream mid-flight,
        so an aborted backup never leaves a stopped container behind on the
        (possibly remote) engine host.
        """
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
        progress: Callable[[int], None] | None = None,
    ) -> str:
        """Export ``volume`` to ``local_tar_path`` as a gzipped tar.

        Runs a throwaway container that mounts the volume read-only and tars its
        contents to stdout. The byte stream is piped to the client (over SSH for
        remote sessions), written to ``local_tar_path``, and hashed as it flows
        through — so nothing large ever touches the engine host and the archive
        is read exactly once. The helper is always removed, even on failure.

        Args:
            volume: Named volume to export (mounted at ``/pvc``).
            image: Container image to run the helper with (the session's own
                image, already present on the engine host — must provide ``tar``).
            local_tar_path: Destination path on the local machine.
            exclude: HOME-relative ``tar --exclude`` globs (e.g. credential
                files) to omit from the archive.
            progress: Optional callback invoked with the cumulative number of
                bytes written so far, for a live progress display.

        Returns:
            The archive's SHA-256 hex digest (computed on the client as the
            stream is written, so it also verifies transfer integrity).
        """
        # `--entrypoint sh` overrides any entrypoint baked into the session
        # image; the container tars the read-only volume to stdout, which we
        # stream straight into the local file (no archive file on the engine
        # host), hashing it in the same pass. stream_run owns the process
        # lifecycle and raises if the container exits non-zero.
        hasher = hashlib.sha256()
        written = 0
        with (
            self._helper_container() as helper,
            self._engine.stream_run(
                "run",
                "--name",
                helper,
                "--entrypoint",
                "sh",
                "-v",
                f"{volume}:/pvc:ro",
                image,
                "-c",
                _tar_stream_script(exclude or []),
            ) as stream,
            open(local_tar_path, "wb") as out,
        ):
            for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
                out.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
                if progress is not None:
                    progress(written)
        return hasher.hexdigest()


def _tar_stream_script(exclude: list[str]) -> str:
    """Build a ``sh -c`` script that tars ``/pvc`` to stdout."""
    parts = ["tar", "-czf", "-"]
    for pattern in exclude:
        parts.extend(["--exclude", shlex.quote(pattern)])
    parts.extend(["-C", "/pvc", "."])
    return " ".join(parts)
