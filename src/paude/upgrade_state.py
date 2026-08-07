"""Durable manifest for crash-safe (resumable) session upgrades.

``paude upgrade`` tears down a session's container before recreating it, and
that container's labels are the only place the session's configuration lives.
If the upgrade is interrupted (e.g. ``CTRL+C``) after the container is removed
but before the replacement exists, the configuration is gone and the upgrade
cannot be retried.

To avoid that, upgrade writes the fully-resolved configuration here *before*
any teardown and deletes it only once the upgrade succeeds. A leftover manifest
means an upgrade was interrupted; re-running ``paude upgrade <name>`` reads the
manifest and finishes the job.

The manifest lives on the *local* host (alongside the session registry) even
for remote/SSH sessions, so recovery works even if the connection drops.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from paude.config.user_config import _paude_config_dir
from paude.json_store import atomic_write_json, read_json

logger = logging.getLogger(__name__)


@dataclass
class UpgradeManifest:
    """Everything needed to recreate a session without its container labels.

    Mirrors the configuration derived from container labels in
    :func:`paude.cli.upgrade._upgrade_podman` so a resumed upgrade can rebuild
    the session even after the original container (and its labels) are gone.
    """

    name: str
    to_version: str
    created_at: str
    workspace: str
    agent: str = "claude"
    provider: str | None = None
    agent_providers: list[tuple[str, str]] = field(default_factory=list)
    credential_providers: list[str] = field(default_factory=list)
    gpu: str | None = None
    yolo: bool = False
    otel_endpoint: str | None = None
    allowed_domains: list[str] | None = None
    proxy_image: str | None = None


def _manifests_path() -> Path:
    """Return the path to the upgrade manifests file."""
    return _paude_config_dir() / "upgrades.json"


def _load_all(path: Path) -> dict[str, dict[str, Any]]:
    """Load all manifests, returning an empty dict when missing or corrupt."""
    manifests = read_json(path).get("upgrades", {})
    return manifests if isinstance(manifests, dict) else {}


def save(manifest: UpgradeManifest, path: Path | None = None) -> None:
    """Persist (or overwrite) the upgrade manifest for a session atomically."""
    target = path or _manifests_path()
    manifests = _load_all(target)
    manifests[manifest.name] = asdict(manifest)
    atomic_write_json(target, {"upgrades": manifests})


def load(name: str, path: Path | None = None) -> UpgradeManifest | None:
    """Return the in-progress upgrade manifest for a session, or None."""
    target = path or _manifests_path()
    raw = _load_all(target).get(name)
    if raw is None:
        return None
    try:
        entry = dict(raw)
        # JSON stores tuples as lists; normalise agent_providers back to tuples.
        entry["agent_providers"] = [
            tuple(item) for item in (entry.get("agent_providers") or [])
        ]
        return UpgradeManifest(**entry)
    except (TypeError, ValueError):
        logger.warning("Ignoring corrupt upgrade manifest for '%s'", name)
        return None


def delete(name: str, path: Path | None = None) -> None:
    """Remove a session's upgrade manifest if present (no error if absent)."""
    target = path or _manifests_path()
    manifests = _load_all(target)
    if name in manifests:
        del manifests[name]
        atomic_write_json(target, {"upgrades": manifests})
