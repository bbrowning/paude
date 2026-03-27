"""User-level default configuration for paude."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OpenShiftDefaults:
    """OpenShift-specific user defaults."""

    context: str | None = None
    namespace: str | None = None
    resources: dict[str, dict[str, str]] | None = None
    build_resources: dict[str, dict[str, str]] | None = None


@dataclass
class UserDefaults:
    """User-level default configuration.

    All fields are Optional (None = not set). When not set, the
    built-in defaults apply.
    """

    backend: str | None = None
    agent: str | None = None
    yolo: bool | None = None
    git: bool | None = None
    pvc_size: str | None = None
    credential_timeout: int | None = None
    platform: str | None = None
    gpu: str | None = None
    allowed_domains: list[str] = field(default_factory=list)
    openshift: OpenShiftDefaults = field(default_factory=OpenShiftDefaults)


# Keys allowed in the top-level "defaults" object
_KNOWN_KEYS = {
    "backend",
    "agent",
    "yolo",
    "git",
    "pvc-size",
    "credential-timeout",
    "platform",
    "gpu",
    "allowed-domains",
    "openshift",
}

# Keys allowed inside "openshift"
_KNOWN_OPENSHIFT_KEYS = {"context", "namespace", "resources", "build-resources"}


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
    openshift_data = data.get("openshift", {})
    if isinstance(openshift_data, dict):
        _warn_unknown_keys(openshift_data, _KNOWN_OPENSHIFT_KEYS, path)

        raw_resources = openshift_data.get("resources")
        resources: dict[str, dict[str, str]] | None = None
        if raw_resources is not None:
            if isinstance(raw_resources, dict):
                resources = raw_resources
            else:
                print(
                    f"Warning: 'openshift.resources' in {path}"
                    " is not an object, ignoring",
                    file=sys.stderr,
                )

        raw_build = openshift_data.get("build-resources")
        build_resources: dict[str, dict[str, str]] | None = None
        if raw_build is not None:
            if isinstance(raw_build, dict):
                build_resources = raw_build
            else:
                print(
                    f"Warning: 'openshift.build-resources' in {path}"
                    " is not an object, ignoring",
                    file=sys.stderr,
                )

        openshift = OpenShiftDefaults(
            context=openshift_data.get("context"),
            namespace=openshift_data.get("namespace"),
            resources=resources,
            build_resources=build_resources,
        )
    else:
        openshift = OpenShiftDefaults()

    allowed_domains = data.get("allowed-domains", [])
    if not isinstance(allowed_domains, list):
        allowed_domains = []

    return UserDefaults(
        backend=data.get("backend"),
        agent=data.get("agent"),
        yolo=data.get("yolo"),
        git=data.get("git"),
        pvc_size=data.get("pvc-size"),
        credential_timeout=data.get("credential-timeout"),
        platform=data.get("platform"),
        gpu=data.get("gpu"),
        allowed_domains=allowed_domains,
        openshift=openshift,
    )
