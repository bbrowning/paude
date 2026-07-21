"""Local session registry for offline session tracking."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.base import Session
from paude.config.user_config import _paude_config_dir

logger = logging.getLogger(__name__)


@dataclass
class RegistryEntry:
    """A session entry persisted in the local registry.

    Attributes:
        name: Session name.
        backend_type: Container engine ("podman" or "docker").
        workspace: Resolved absolute path as string.
        agent: Agent name (e.g. "claude", "gemini").
        created_at: ISO timestamp of session creation.
        engine: Container engine binary ("podman" or "docker").
    """

    name: str
    backend_type: str
    workspace: str
    agent: str
    created_at: str
    engine: str = "podman"
    ssh_host: str | None = None
    ssh_key: str | None = None
    remote_config_dir: str | None = None
    paude_version: str | None = None

    def to_session(self, status: str = "unknown") -> Session:
        """Convert this entry to a Session object."""
        return Session(
            name=self.name,
            status=status,
            workspace=Path(self.workspace),
            created_at=self.created_at,
            backend_type=self.backend_type,
            agent=self.agent,
            version=self.paude_version,
        )


def _registry_path() -> Path:
    """Return the path to the session registry file."""
    return _paude_config_dir() / "sessions.json"


class SessionRegistry:
    """Local file-based session registry.

    Stores session metadata in a JSON file so that sessions can be
    listed even when the backend is unreachable.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _registry_path()

    def load(self) -> dict[str, RegistryEntry]:
        """Load all entries from the registry file.

        Returns an empty dict if the file is missing or corrupt.
        """
        try:
            data = json.loads(self._path.read_text())
            sessions = data.get("sessions", {})
            entries: dict[str, RegistryEntry] = {}
            for name, raw_entry in sessions.items():
                entry = dict(raw_entry)
                if entry.get("backend_type") == "openshift":
                    logger.warning(
                        "Ignoring legacy OpenShift session '%s'; this backend is no "
                        "longer supported",
                        name,
                    )
                    continue
                entry.pop("openshift_context", None)
                entry.pop("openshift_namespace", None)
                entries[name] = RegistryEntry(**entry)
            return entries
        except (FileNotFoundError, json.JSONDecodeError, TypeError, KeyError):
            return {}

    def _write_raw(self, data: dict[str, Any]) -> None:
        """Write raw JSON data to the registry file atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _save(self, entries: dict[str, RegistryEntry]) -> None:
        """Write entries to the registry file atomically."""
        data = {"sessions": {k: asdict(v) for k, v in entries.items()}}
        self._write_raw(data)

    def register(
        self,
        session: Session,
        ssh_host: str | None = None,
        ssh_key: str | None = None,
        remote_config_dir: str | None = None,
        paude_version: str | None = None,
    ) -> None:
        """Add or update a session in the registry."""
        from paude.backends.shared import is_local_backend

        entries = self.load()
        # For local backends, engine == backend_type
        engine = (
            session.backend_type if is_local_backend(session.backend_type) else "podman"
        )
        entries[session.name] = RegistryEntry(
            name=session.name,
            backend_type=session.backend_type,
            workspace=str(session.workspace),
            agent=session.agent,
            created_at=session.created_at or datetime.now(UTC).isoformat(),
            engine=engine,
            ssh_host=ssh_host,
            ssh_key=ssh_key,
            remote_config_dir=remote_config_dir,
            paude_version=paude_version,
        )
        self._save(entries)

    def unregister(self, name: str) -> bool:
        """Remove a session from the registry by name.

        Operates on raw JSON so it can remove any entry, including ones
        that ``load()`` would filter (e.g. legacy backends).

        Returns True if an entry was removed, False if not found.
        """
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        sessions = data.get("sessions", {})
        if name not in sessions:
            return False
        del sessions[name]
        data["sessions"] = sessions
        self._write_raw(data)
        return True

    def get(self, name: str) -> RegistryEntry | None:
        """Get a single registry entry by name."""
        return self.load().get(name)

    def list_entries(self) -> list[RegistryEntry]:
        """Return all registry entries."""
        return list(self.load().values())


def merge_registry_with_live(
    registry: SessionRegistry,
    live_sessions: list[Session],
    reachable_backends: set[str],
) -> list[Session]:
    """Union/dedupe registry entries with live-discovered sessions.

    - Live sessions always win when present in both.
    - Registry-only sessions with unreachable backend get status "unreachable".
    - Registry-only sessions with reachable backend get status "stale".
    - Live-only sessions are backfilled into the registry.

    Args:
        registry: SessionRegistry instance (used for load + backfill).
        live_sessions: Sessions discovered from live backend queries.
        reachable_backends: Set of backend types that responded successfully.

    Returns:
        Merged list of Session objects.
    """
    entries = registry.load()
    live_by_name = {s.name: s for s in live_sessions}
    merged: list[Session] = []

    # Process all registry entries
    for name, entry in entries.items():
        if name in live_by_name:
            # Live data wins
            merged.append(live_by_name[name])
        elif entry.backend_type in reachable_backends:
            # Backend was reachable but session wasn't found — stale
            logger.warning(
                "Session '%s' not found in %s — may have been deleted externally",
                name,
                entry.backend_type,
            )
            merged.append(entry.to_session(status="stale"))
        else:
            # Backend unreachable
            merged.append(entry.to_session(status="unreachable"))

    # Include live-only sessions and backfill into registry in one write
    backfill_entries: list[Session] = []
    for name, session in live_by_name.items():
        if name not in entries:
            merged.append(session)
            backfill_entries.append(session)

    if backfill_entries:
        for session in backfill_entries:
            entries[session.name] = RegistryEntry(
                name=session.name,
                backend_type=session.backend_type,
                workspace=str(session.workspace),
                agent=session.agent,
                created_at=session.created_at or datetime.now(UTC).isoformat(),
                paude_version=session.version,
            )
        registry._save(entries)

    return merged
