"""Small shared helpers for durable JSON files under the paude config dir.

Both the session registry (:mod:`paude.registry`) and the upgrade manifest
store (:mod:`paude.upgrade_state`) persist a single JSON document atomically.
This module holds the one copy of that durability-critical machinery.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically (tempfile + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> dict[str, Any]:
    """Return the parsed JSON object at ``path``, or ``{}`` if missing/corrupt."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        # OSError covers a missing file (FileNotFoundError) as well as an
        # unreadable one (PermissionError) or a path replaced by a directory
        # (IsADirectoryError) — all treated as "no usable data" per contract.
        return {}
    return data if isinstance(data, dict) else {}
