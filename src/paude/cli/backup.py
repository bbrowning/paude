"""Backup and restore commands for paude sessions.

``paude backup`` snapshots a session into a ``<name>-<timestamp>.paude/``
bundle directory: ``manifest.json`` (the session's identity/config) plus
``pvc.tar.gz`` (the ``/pvc`` data volume). ``paude restore`` (sketched here,
not yet implemented) will rebuild a session from such a bundle.

A backup captures only what is irreplaceable — the data volume — and the config
needed to recreate the container around it. The proxy sidecar, network,
CA/auth volumes, and secrets are all reconstructed from the host environment on
``start``, so they are deliberately not included. Known agent credential files
are always stripped so a bundle never persists a live token to disk.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated

import typer

from paude import backup_state
from paude.backup_state import (
    MANIFEST_FILENAME,
    VOLUME_ARCHIVE_FILENAME,
    BackupFormatError,
    BackupManifest,
)
from paude.cli.app import BackendType, app
from paude.transport.ssh import SshTransport

if TYPE_CHECKING:
    from paude.backends.podman.backend import PodmanBackend
    from paude.backends.podman.volume_archive import VolumeArchiver


def _default_backups_dir() -> Path:
    """Return the default directory for backup bundles."""
    from paude.config.user_config import _paude_config_dir

    return _paude_config_dir() / "backups"


def _default_bundle_name(name: str, now: datetime) -> str:
    """Return the default ``<name>-<timestamp>.paude`` bundle name."""
    return f"{name}-{now.strftime('%Y%m%dT%H%M%SZ')}.paude"


def _resolve_output_path(output: str | None, name: str, now: datetime) -> Path:
    """Resolve the bundle *directory* path, defaulting name/dir as needed.

    A bundle is a ``<name>-<timestamp>.paude/`` directory. If ``output`` is an
    existing directory, the default-named bundle is created inside it; otherwise
    ``output`` is treated as the exact bundle directory path to create.
    """
    default_name = _default_bundle_name(name, now)
    if output is None:
        dest_dir = _default_backups_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir / default_name
    output_path = Path(output).expanduser()
    if output_path.is_dir():
        output_path = output_path / default_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return output_path


def _resolve_remote_output_path(
    transport: SshTransport, output: str | None, name: str, now: datetime
) -> str:
    """Resolve the bundle *directory* path on the engine's remote host.

    Mirrors :func:`_resolve_output_path`, but every filesystem check is a
    small SSH round-trip against the remote host instead of a local ``Path``
    call — used for ``paude backup --remote-only``, where the bundle is never
    downloaded to the client. Round-trips are combined where possible (e.g.
    resolving the default directory and creating it in one call): this
    feature exists to go easy on a slow link, so each extra SSH connection
    has a real cost.
    """
    default_name = _default_bundle_name(name, now)
    if output is None:
        script = (
            'dir="${XDG_CONFIG_HOME:-$HOME/.config}/paude/backups"; '
            'mkdir -p "$dir" && printf "%s" "$dir"'
        )
        result = transport.run(["sh", "-c", script])
        dest_dir = result.stdout.strip()
        return f"{dest_dir}/{default_name}"

    is_dir = transport.run(["test", "-d", output], check=False).returncode == 0
    output_path = f"{output.rstrip('/')}/{default_name}" if is_dir else output
    parent = str(PurePosixPath(output_path).parent)
    transport.run(["mkdir", "-p", parent])
    return output_path


def _build_manifest(
    backend: PodmanBackend,
    name: str,
    image: str,
    now: datetime,
) -> BackupManifest:
    """Assemble the backup manifest from the session's labels and registry entry."""
    from paude import __version__
    from paude.backends.labels import (
        PAUDE_LABEL_DOMAINS,
        PAUDE_LABEL_GPU,
        PAUDE_LABEL_OTEL_ENDPOINT,
        PAUDE_LABEL_PROXY_IMAGE,
        PAUDE_LABEL_YOLO,
    )
    from paude.backends.podman.helpers import (
        build_session_from_container,
        find_container_by_session_name,
    )
    from paude.registry import SessionRegistry

    # Fetch the container once and derive both the Session and its labels from
    # it, rather than round-tripping to the backend twice (matters on SSH).
    container = find_container_by_session_name(backend._runner, name)
    if container is None:
        raise RuntimeError(f"Session '{name}' not found.")
    labels = container.get("Labels", {}) or {}
    session = build_session_from_container(
        name, container, backend._runner, backend.backend_type
    )

    domains_label = labels.get(PAUDE_LABEL_DOMAINS)
    allowed_domains: list[str] | None = None
    if domains_label is not None:
        allowed_domains = domains_label.split(",") if domains_label else []

    entry = SessionRegistry().get(name)

    return BackupManifest(
        name=name,
        workspace=str(session.workspace),
        created_at=now.isoformat(),
        source_paude_version=__version__,
        session_created_at=session.created_at or None,
        agent=session.agent,
        provider=session.provider,
        agent_providers=list(session.agent_providers),
        credential_providers=list(session.credential_providers),
        gpu=labels.get(PAUDE_LABEL_GPU) or None,
        yolo=labels.get(PAUDE_LABEL_YOLO) == "1",
        otel_endpoint=labels.get(PAUDE_LABEL_OTEL_ENDPOINT) or None,
        allowed_domains=allowed_domains,
        proxy_image=labels.get(PAUDE_LABEL_PROXY_IMAGE) or None,
        image=image,
        backend_type=session.backend_type,
        engine=entry.engine if entry else session.backend_type,
        ssh_host=entry.ssh_host if entry else None,
        ssh_key=entry.ssh_key if entry else None,
        remote_config_dir=entry.remote_config_dir if entry else None,
    )


@app.command("backup")
def session_backup(
    name: Annotated[
        str | None,
        typer.Argument(help="Session to back up (auto-detected if omitted)."),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option(
            "--output",
            "-o",
            help=(
                "Bundle directory path, or an existing directory to create the "
                "default-named bundle in. Defaults to ~/.config/paude/backups/."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Proceed even if the destination looks low on free disk space.",
        ),
    ] = False,
    remote_only: Annotated[
        bool,
        typer.Option(
            "--remote-only",
            help=(
                "Keep the bundle on the session's remote host instead of "
                "downloading it (requires a session created with --host)."
            ),
        ),
    ] = False,
) -> None:
    """Back up a stopped session to a portable bundle.

    Captures the session's ``/pvc`` data volume (workspace + agent state) and a
    config manifest into a ``<name>-<timestamp>.paude/`` directory. The session
    must be stopped first (``paude stop NAME``) so the snapshot is consistent.
    Agent credential files are always excluded. ``--remote-only`` writes the
    bundle on the session's remote host instead, so nothing large crosses the
    SSH link back to this machine.
    """
    from paude.backends import PodmanBackend
    from paude.backends.podman.helpers import container_name, volume_name
    from paude.backends.podman.volume_archive import VolumeArchiver
    from paude.cli.helpers import (
        _auto_select_session,
        _get_backend_instance,
        find_session_backend,
    )

    # Resolve the session + backend (mirrors stop/cp conventions).
    if name is None:
        session, backend_obj = _auto_select_session(
            no_sessions_hints=[
                "No sessions found to back up.",
                "Create one with: paude create",
            ],
            multi_hint_format="  paude backup {name}  # {backend_type}",
        )
        name = session.name
    elif backend is not None:
        backend_obj = _get_backend_instance(backend)
    else:
        result = find_session_backend(name)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        _, backend_obj = result

    if not isinstance(backend_obj, PodmanBackend):
        typer.echo("Unsupported backend for backup.", err=True)
        raise typer.Exit(1)

    cname = container_name(name)
    vname = volume_name(name)

    if not backend_obj._runner.container_exists(cname):
        typer.echo(f"Session '{name}' not found.", err=True)
        raise typer.Exit(1)

    # Refuse to back up a running session: a live container may be writing to
    # git/sqlite/dolt, which would tear the snapshot. No auto-stop.
    if backend_obj._runner.container_running(cname):
        typer.echo(
            f"Session '{name}' is running. Stop it first: paude stop {name}",
            err=True,
        )
        raise typer.Exit(1)

    image = backend_obj._runner.get_container_image(cname)
    if not image:
        typer.echo(
            f"Could not determine the container image for session '{name}'.",
            err=True,
        )
        raise typer.Exit(1)

    now = datetime.now(UTC)
    archiver = VolumeArchiver(backend_obj._engine)
    backup_fn = _run_remote_backup if remote_only else _run_local_backup
    backup_fn(name, backend_obj, archiver, vname, image, output, force, now)


def _finalize_backup(
    backend_obj: PodmanBackend,
    name: str,
    image: str,
    now: datetime,
    location_label: str,
    write: Callable[[BackupManifest, list[str]], None],
) -> None:
    """Build the manifest + exclude list, run ``write``, and print the result.

    Shared between the local and remote-only backup paths so manifest
    building, error translation, and the success message aren't duplicated.
    """
    from paude.backends import SessionNotFoundError
    from paude.backends.podman.helpers import get_session_composition
    from paude.cli.upgrade_persistence import credential_exclude_globs

    try:
        composition = get_session_composition(backend_obj._runner, name)
        exclude = credential_exclude_globs(composition)
        manifest = _build_manifest(backend_obj, name, image, now)
        write(manifest, exclude)
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error backing up session: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Backed up session '{name}' to {location_label}")
    if exclude:
        typer.echo(
            "  Credential files were excluded; re-login inside the session after "
            "restore for agents that authenticate in-container (e.g. Gemini, "
            "Cursor).",
        )


def _run_local_backup(
    name: str,
    backend_obj: PodmanBackend,
    archiver: VolumeArchiver,
    vname: str,
    image: str,
    output: str | None,
    force: bool,
    now: datetime,
) -> None:
    """Resolve, preflight, and write a bundle on the local machine."""
    output_path = _resolve_output_path(output, name, now)
    if output_path.exists():
        typer.echo(f"Backup destination already exists: {output_path}", err=True)
        raise typer.Exit(1)

    _preflight_disk_space(archiver, vname, image, output_path.parent, force=force)
    _finalize_backup(
        backend_obj,
        name,
        image,
        now,
        str(output_path),
        lambda manifest, exclude: _write_bundle(
            archiver, vname, image, exclude, manifest, output_path
        ),
    )


def _run_remote_backup(
    name: str,
    backend_obj: PodmanBackend,
    archiver: VolumeArchiver,
    vname: str,
    image: str,
    output: str | None,
    force: bool,
    now: datetime,
) -> None:
    """Resolve, preflight, and write a bundle on the session's remote host."""
    transport = backend_obj._engine.transport
    if not backend_obj._engine.is_remote or not isinstance(transport, SshTransport):
        typer.echo(
            f"Session '{name}' does not run on a remote host; --remote-only "
            "requires a session created with --host.",
            err=True,
        )
        raise typer.Exit(1)

    remote_path = _resolve_remote_output_path(transport, output, name, now)
    location_label = f"{transport.host_label}:{remote_path}"
    if transport.run(["test", "-e", remote_path], check=False).returncode == 0:
        typer.echo(f"Backup destination already exists: {location_label}", err=True)
        raise typer.Exit(1)

    remote_dest_dir = str(PurePosixPath(remote_path).parent)
    _preflight_disk_space_remote(
        archiver, vname, image, transport, remote_dest_dir, force=force
    )
    _finalize_backup(
        backend_obj,
        name,
        image,
        now,
        location_label,
        lambda manifest, exclude: _write_bundle_remote(
            archiver, transport, vname, image, exclude, manifest, remote_path
        ),
    )


def _preflight_disk_space(
    archiver: VolumeArchiver,
    vname: str,
    image: str,
    dest_dir: Path,
    *,
    force: bool,
) -> None:
    """Warn and require ``--force`` if the destination looks short on space.

    Uses the volume's uncompressed size as a conservative estimate (the
    compressed archive is smaller). No-op if the size can't be determined.
    """
    if force:
        return
    raw = archiver.volume_size_bytes(vname, image)
    if raw is None:
        return
    free = shutil.disk_usage(dest_dir).free
    _require_enough_space(raw, free, str(dest_dir), remote=False)


def _preflight_disk_space_remote(
    archiver: VolumeArchiver,
    vname: str,
    image: str,
    transport: SshTransport,
    remote_dest_dir: str,
    *,
    force: bool,
) -> None:
    """Remote-host counterpart to :func:`_preflight_disk_space`.

    Free space comes from ``df`` on the engine's remote host instead of
    ``shutil.disk_usage``, since the bundle for ``--remote-only`` never lands
    on the local filesystem.
    """
    if force:
        return
    raw = archiver.volume_size_bytes(vname, image)
    if raw is None:
        return
    free = transport.free_bytes(remote_dest_dir)
    if free is None:
        return
    _require_enough_space(
        raw, free, f"{transport.host_label}:{remote_dest_dir}", remote=True
    )


def _require_enough_space(
    raw: int, free: int, location_label: str, *, remote: bool
) -> None:
    """Shared "is there enough room" check for the local/remote preflights."""
    if free >= raw:
        return
    where = " on the remote host" if remote else ""
    typer.echo(
        f"Not enough free space at {location_label}: volume is ~{_human(raw)} "
        f"(uncompressed) but only {_human(free)} is free{where}. The compressed "
        "backup will likely be smaller. Re-run with --force to proceed.",
        err=True,
    )
    raise typer.Exit(1)


def _human(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _format_clock(seconds: float) -> str:
    """Format a duration as a ``M:SS`` (or ``H:MM:SS`` past an hour) clock."""
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class _ArchiveProgress:
    """Render live archive progress to a TTY: bytes written, rate, elapsed.

    Deliberately shows no percentage or ETA — the gzip ratio isn't known up
    front, so a percentage against the uncompressed volume size would mislead.
    Throughput plus a running total honestly answers "is it alive and how fast".
    A no-op when stderr isn't a TTY, to keep piped/redirected output clean.
    """

    _MIN_INTERVAL = 0.5  # seconds between redraws

    def __init__(self) -> None:
        self._enabled = sys.stderr.isatty()
        self._start = time.monotonic()
        self._last_render = 0.0  # nonzero once we've drawn a line

    @property
    def enabled(self) -> bool:
        """Whether progress will actually render (stderr is a TTY)."""
        return self._enabled

    def update(self, written: int) -> None:
        """Progress callback: ``written`` is the cumulative byte count so far."""
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last_render < self._MIN_INTERVAL:
            return
        self._last_render = now
        elapsed = now - self._start
        rate = written / elapsed if elapsed > 0 else 0.0
        # \033[K clears any leftover chars from a previous, longer line.
        line = (
            f"\r  {_human(written)} written • "
            f"{_human(int(rate))}/s • {_format_clock(elapsed)}\033[K"
        )
        print(line, end="", file=sys.stderr, flush=True)

    def finish(self) -> None:
        """End the progress line so later output starts on a fresh line."""
        if self._last_render:
            print(file=sys.stderr)


def _write_bundle(
    archiver: VolumeArchiver,
    vname: str,
    image: str,
    exclude: list[str],
    manifest: BackupManifest,
    output_path: Path,
) -> None:
    """Build the bundle directory atomically at ``output_path``.

    Writes ``pvc.tar.gz`` and ``manifest.json`` into a temp sibling directory,
    then atomically renames it into place — so the volume archive is written to
    the destination filesystem exactly once (no outer-tar re-copy).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix=".paude-backup-", dir=output_path.parent))
    try:
        typer.echo(f"Archiving volume {vname}...", err=True)
        pvc_tar = tmp_dir / VOLUME_ARCHIVE_FILENAME
        # export_volume streams the archive straight to pvc_tar, hashing it in
        # the same pass — the multi-GB file is never staged on the engine host
        # nor read a second time just to checksum it.
        progress = _ArchiveProgress()
        try:
            manifest.archive_sha256 = archiver.export_volume(
                vname, image, str(pvc_tar), exclude=exclude, progress=progress.update
            )
        finally:
            progress.finish()
        os.chmod(pvc_tar, 0o600)

        manifest_path = tmp_dir / MANIFEST_FILENAME
        manifest_path.write_text(manifest.to_json())
        os.chmod(manifest_path, 0o600)

        os.chmod(tmp_dir, 0o700)
        os.replace(tmp_dir, output_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _write_bundle_remote(
    archiver: VolumeArchiver,
    transport: SshTransport,
    vname: str,
    image: str,
    exclude: list[str],
    manifest: BackupManifest,
    remote_output_path: str,
) -> None:
    """Remote-host counterpart to :func:`_write_bundle`.

    Builds the bundle directory atomically on the engine's remote host, the
    same way :func:`_write_bundle` does locally, so nothing large ever
    crosses the SSH link back to the client. Round-trips are combined where
    the shell allows it (e.g. writing the manifest and chmod-ing it in one
    call): each is a full SSH connection on a link this feature exists to go
    easy on.
    """
    parent = str(PurePosixPath(remote_output_path).parent)
    mktemp_result = transport.run(["mktemp", "-d", f"{parent}/.paude-backup-XXXXXX"])
    tmp_dir = mktemp_result.stdout.strip()
    try:
        typer.echo(f"Archiving volume {vname} on {transport.host_label}...", err=True)
        pvc_tar = f"{tmp_dir}/{VOLUME_ARCHIVE_FILENAME}"
        progress = _ArchiveProgress()
        try:
            # Polling for progress is itself an SSH round-trip, unlike the
            # local path's free in-process callback -- skip it when nothing
            # will render (piped/redirected output).
            manifest.archive_sha256 = archiver.export_volume_to_remote_file(
                vname,
                image,
                pvc_tar,
                exclude=exclude,
                progress=progress.update if progress.enabled else None,
            )
        finally:
            progress.finish()

        manifest_path = f"{tmp_dir}/{MANIFEST_FILENAME}"
        quoted_manifest = shlex.quote(manifest_path)
        transport.run(
            ["sh", "-c", f"cat > {quoted_manifest} && chmod 0600 {quoted_manifest}"],
            input=manifest.to_json(),
        )

        quoted_tmp_dir = shlex.quote(tmp_dir)
        transport.run(
            [
                "sh",
                "-c",
                f"chmod 0700 {quoted_tmp_dir} && "
                f"mv {quoted_tmp_dir} {shlex.quote(remote_output_path)}",
            ]
        )
    except Exception:
        # Only reachable if something above failed before the mv -- on
        # success tmp_dir no longer exists, so there's nothing to clean up
        # (and no reason to pay for the round trip).
        transport.run(["rm", "-rf", tmp_dir], check=False)
        raise


def _read_manifest(bundle_dir: Path) -> BackupManifest:
    """Read and validate ``manifest.json`` from a bundle directory."""
    manifest_path = bundle_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise BackupFormatError(f"Bundle is missing its {MANIFEST_FILENAME} file.")
    return backup_state.loads(manifest_path.read_text())


@app.command("restore")
def session_restore(
    bundle_path: Annotated[
        str,
        typer.Argument(help="Path to a .paude backup bundle directory."),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Restore under a different session name."),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option("--backend", help="Target backend (default: from the bundle)."),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option("--host", help="Restore onto a remote host over SSH."),
    ] = None,
    ssh_key: Annotated[
        str | None,
        typer.Option("--ssh-key", help="SSH key for --host."),
    ] = None,
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm", help="Overwrite an existing session of the same name."
        ),
    ] = False,
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Force an image rebuild during restore."),
    ] = False,
) -> None:
    """Restore a session from a backup bundle. (Not yet implemented.)

    This validates the bundle and prints the restore it *would* perform. The
    implementation reuses the ``paude upgrade`` blueprint: create the ``/pvc``
    volume from ``pvc.tar.gz``, then rebuild the container via
    ``create_session(reuse_volume=True)`` + ``start_session_no_attach`` and
    re-register the session. Credential files stripped at backup time mean
    in-container-login agents (e.g. Gemini, Cursor) must be re-authenticated
    after restore.
    """
    path = Path(bundle_path).expanduser()
    if not path.is_dir():
        typer.echo(f"Backup bundle not found: {path}", err=True)
        raise typer.Exit(1)

    try:
        manifest = _read_manifest(path)
    except BackupFormatError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    target = name or manifest.name
    target_backend = backend.value if backend is not None else manifest.engine

    typer.echo(f"Restore plan for bundle {path}:")
    typer.echo(f"  Session name:   {target}")
    typer.echo(f"  Backend:        {target_backend}")
    typer.echo(f"  Agent:          {manifest.agent}")
    typer.echo(f"  Workspace:      {manifest.workspace}")
    if host:
        typer.echo(f"  Target host:    {host}")
    typer.echo(
        "\nWould: create the /pvc volume from the bundle, recreate the container "
        "(reusing the volume), start it, and register the session."
    )
    typer.echo(
        "\n'paude restore' is not yet implemented; this is a dry run.",
        err=True,
    )
    raise typer.Exit(2)
