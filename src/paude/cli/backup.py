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
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
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

if TYPE_CHECKING:
    from paude.backends.podman.backend import PodmanBackend
    from paude.backends.podman.volume_archive import VolumeArchiver


def _default_backups_dir() -> Path:
    """Return the default directory for backup bundles."""
    from paude.config.user_config import _paude_config_dir

    return _paude_config_dir() / "backups"


def _resolve_output_path(output: str | None, name: str, now: datetime) -> Path:
    """Resolve the bundle *directory* path, defaulting name/dir as needed.

    A bundle is a ``<name>-<timestamp>.paude/`` directory. If ``output`` is an
    existing directory, the default-named bundle is created inside it; otherwise
    ``output`` is treated as the exact bundle directory path to create.
    """
    default_name = f"{name}-{now.strftime('%Y%m%dT%H%M%SZ')}.paude"
    if output is None:
        dest_dir = _default_backups_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir / default_name
    output_path = Path(output).expanduser()
    if output_path.is_dir():
        output_path = output_path / default_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
) -> None:
    """Back up a stopped session to a portable bundle.

    Captures the session's ``/pvc`` data volume (workspace + agent state) and a
    config manifest into a ``<name>-<timestamp>.paude/`` directory. The session
    must be stopped first (``paude stop NAME``) so the snapshot is consistent.
    Agent credential files are always excluded.
    """
    from paude.backends import PodmanBackend, SessionNotFoundError
    from paude.backends.podman.helpers import (
        container_name,
        get_session_composition,
        volume_name,
    )
    from paude.backends.podman.volume_archive import VolumeArchiver
    from paude.cli.helpers import (
        _auto_select_session,
        _get_backend_instance,
        find_session_backend,
    )
    from paude.cli.upgrade_persistence import credential_exclude_globs

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
    output_path = _resolve_output_path(output, name, now)
    if output_path.exists():
        typer.echo(f"Backup destination already exists: {output_path}", err=True)
        raise typer.Exit(1)

    archiver = VolumeArchiver(backend_obj._engine)
    _preflight_disk_space(archiver, vname, image, output_path.parent, force=force)

    try:
        composition = get_session_composition(backend_obj._runner, name)
        exclude = credential_exclude_globs(composition)
        manifest = _build_manifest(backend_obj, name, image, now)
        _write_bundle(archiver, vname, image, exclude, manifest, output_path)
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error backing up session: {e}", err=True)
        raise typer.Exit(1) from None

    typer.echo(f"Backed up session '{name}' to {output_path}")
    if exclude:
        typer.echo(
            "  Credential files were excluded; re-login inside the session after "
            "restore for agents that authenticate in-container (e.g. Gemini, "
            "Cursor).",
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
    if free < raw:
        typer.echo(
            f"Not enough free space at {dest_dir}: volume is ~{_human(raw)} "
            f"(uncompressed) but only {_human(free)} is free. The compressed "
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
        # export_volume hashes the archive in the same pass it creates it, so the
        # multi-GB file isn't read a second time just to checksum it.
        manifest.archive_sha256 = archiver.export_volume(
            vname, image, str(pvc_tar), exclude=exclude
        )
        os.chmod(pvc_tar, 0o600)

        manifest_path = tmp_dir / MANIFEST_FILENAME
        manifest_path.write_text(manifest.to_json())
        os.chmod(manifest_path, 0o600)

        os.chmod(tmp_dir, 0o700)
        os.replace(tmp_dir, output_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
