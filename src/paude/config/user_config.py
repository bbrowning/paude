"""User-level default configuration for paude."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UserDefaults:
    """User-level default configuration.

    All fields are Optional (None = not set). When not set, the
    built-in defaults apply.
    """

    backend: str | None = None
    agent: str | None = None
    provider: str | None = None
    yolo: bool | None = None
    git: bool | None = None
    platform: str | None = None
    gpu: str | None = None
    allowed_domains: list[str] = field(default_factory=list)
    otel_endpoint: str | None = None
    forward_ports: list[str] = field(default_factory=list)


# Keys allowed in the top-level "defaults" object
_KNOWN_KEYS = {
    "backend",
    "agent",
    "provider",
    "yolo",
    "git",
    "platform",
    "gpu",
    "allowed-domains",
    "otel-endpoint",
    "forward-ports",
}


def _paude_config_dir() -> Path:
    """Return the paude config directory.

    Uses $XDG_CONFIG_HOME/paude, falling back to ~/.config/paude.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "paude"


def _user_config_path() -> Path:
    """Return the path to the user defaults file."""
    return _paude_config_dir() / "defaults.json"


def load_user_defaults(config_path: Path | None = None) -> UserDefaults:
    """Load user defaults from JSON file.

    Args:
        config_path: Override path (for testing). Uses the standard
            XDG path when None.

    Returns:
        Parsed UserDefaults. Returns empty defaults if the file
        does not exist.
    """
    path = config_path or _user_config_path()

    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return UserDefaults()
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: Cannot read {path}: {e}", file=sys.stderr)
        return UserDefaults()

    defaults_data = data.get("defaults", {})
    if not isinstance(defaults_data, dict):
        print(
            f"Warning: 'defaults' in {path} is not an object, ignoring",
            file=sys.stderr,
        )
        return UserDefaults()

    _warn_unknown_keys(defaults_data, _KNOWN_KEYS, path)

    return _parse_defaults(defaults_data, path)


def _warn_unknown_keys(
    data: dict[str, Any], known: set[str], context: str | Path
) -> None:
    """Warn about unknown keys in the config."""
    unknown = set(data.keys()) - known
    for key in sorted(unknown):
        print(
            f"Warning: Unknown key '{key}' in {context}, ignoring",
            file=sys.stderr,
        )


def _parse_defaults(data: dict[str, Any], path: Path) -> UserDefaults:
    """Parse the 'defaults' object into a UserDefaults dataclass."""
    allowed_domains = data.get("allowed-domains", [])
    if not isinstance(allowed_domains, list):
        allowed_domains = []

    forward_ports = data.get("forward-ports", [])
    if not isinstance(forward_ports, list):
        forward_ports = []
    else:
        forward_ports = [str(p) for p in forward_ports]

    return UserDefaults(
        backend=data.get("backend"),
        agent=data.get("agent"),
        provider=data.get("provider"),
        yolo=data.get("yolo"),
        git=data.get("git"),
        platform=data.get("platform"),
        gpu=data.get("gpu"),
        allowed_domains=allowed_domains,
        otel_endpoint=data.get("otel-endpoint"),
        forward_ports=forward_ports,
    )
