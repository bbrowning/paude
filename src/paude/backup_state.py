"""The manifest stored in a ``paude backup`` bundle.

A backup bundle is a directory (``<name>-<timestamp>.paude/``) with two files:

- ``manifest.json`` — the :class:`BackupManifest` serialized here.
- ``pvc.tar.gz`` — the gzipped contents of the session's ``/pvc`` data volume.

A directory (rather than one wrapping tar) avoids copying the multi-GB volume
archive a second time during assembly, which matters for large volumes.

The manifest carries everything needed to recreate the session's *identity and
configuration* without its container (whose labels are otherwise the only source
of that config): the label-derived config (mirroring
:class:`paude.upgrade_state.UpgradeManifest`) plus the registry-only fields
(``engine``/``ssh_host``/…) that a remote or renamed restore needs.

Unlike the upgrade manifest, this one is not a durable on-host file; it lives
inside the bundle, so this module only provides (de)serialization and format-
version gating, not atomic file persistence.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from paude.backends.labels import SessionSpec, normalize_agent_providers

# Bump when the bundle layout or manifest schema changes incompatibly.
BACKUP_FORMAT_VERSION = 1

MANIFEST_FILENAME = "manifest.json"
VOLUME_ARCHIVE_FILENAME = "pvc.tar.gz"


class BackupFormatError(ValueError):
    """Raised when a bundle's manifest is missing, corrupt, or unsupported."""


@dataclass(kw_only=True)
class BackupManifest(SessionSpec):
    """Everything needed to identify and rebuild a backed-up session.

    The label-derived configuration is inherited from
    :class:`~paude.backends.labels.SessionSpec`; this adds the bundle's own
    identity and integrity fields plus the registry-only fields a remote or
    renamed restore needs. Inheriting rather than embedding keeps ``to_json()``
    flat, so the manifest schema is unchanged.

    Attributes:
        name: Session name at backup time.
        workspace: Local workspace path (string) recorded for the session.
        backup_format_version: Bundle schema version; gated on load.
        created_at: ISO timestamp of when the backup was taken.
        source_paude_version: paude version that produced the bundle.
        session_created_at: ISO timestamp of the original session creation.
        archive_sha256: SHA-256 of ``pvc.tar.gz`` for integrity checks.
        image: The container image the bundle captured.
        backend_type/engine/ssh_host/ssh_key/remote_config_dir: registry-only
            fields needed to reconstruct how to reach the (possibly remote)
            session on restore.
    """

    name: str
    workspace: str
    created_at: str
    source_paude_version: str
    backup_format_version: int = BACKUP_FORMAT_VERSION
    session_created_at: str | None = None
    archive_sha256: str | None = None

    # The image the bundle captured. Not a SessionSpec field: the spec records
    # declared configuration, and this is a build output. Upgrade has no use for
    # it (it always force-rebuilds), but a restore must know what it restored.
    image: str | None = None

    # Registry-only fields (not present in container labels).
    backend_type: str = "podman"
    engine: str = "podman"
    ssh_host: str | None = None
    ssh_key: str | None = None
    remote_config_dir: str | None = None

    def to_json(self) -> str:
        """Serialize to pretty JSON suitable for the bundle's manifest member."""
        return json.dumps(asdict(self), indent=2)


def loads(text: str) -> BackupManifest:
    """Parse a manifest from JSON text, normalizing and gating the version.

    Raises:
        BackupFormatError: If the JSON is invalid, not an object, or declares an
            unsupported ``backup_format_version``.
    """
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackupFormatError(f"Backup manifest is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackupFormatError("Backup manifest is not a JSON object.")

    version = raw.get("backup_format_version")
    if version != BACKUP_FORMAT_VERSION:
        raise BackupFormatError(
            f"Unsupported backup format version {version!r}; "
            f"this paude understands version {BACKUP_FORMAT_VERSION}."
        )

    entry = dict(raw)
    try:
        # JSON stores tuples as lists; normalize agent_providers back to tuples
        # so it round-trips identically to the in-memory dataclass. Strict, so a
        # malformed pair surfaces here rather than as a 1-tuple that blows up
        # later where a caller unpacks `for agent, provider in specs`.
        entry["agent_providers"] = normalize_agent_providers(
            entry.get("agent_providers")
        )
    except ValueError as exc:
        raise BackupFormatError(
            f"Backup manifest has a malformed field: {exc}"
        ) from exc
    try:
        return BackupManifest(**entry)
    except TypeError as exc:
        raise BackupFormatError(
            f"Backup manifest has unexpected fields: {exc}"
        ) from exc
