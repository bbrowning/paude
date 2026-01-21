"""Configuration data models for paude."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

VenvMode = Literal["auto", "none"] | list[str]
PipInstallMode = bool | str


@dataclass
class FeatureSpec:
    """Specification for a dev container feature."""

    url: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class PaudeConfig:
    """Configuration for a paude workspace.

    This dataclass represents the parsed configuration from either:
    - devcontainer.json (standard dev container format)
    - paude.json (paude-specific simple format)
    - No config (defaults)
    """

    config_file: Path | None = None
    config_type: Literal["default", "devcontainer", "paude"] = "default"

    # Image configuration (mutually exclusive with dockerfile)
    base_image: str | None = None

    # Dockerfile configuration (mutually exclusive with base_image)
    dockerfile: Path | None = None
    build_context: Path | None = None

    # Features
    features: list[FeatureSpec] = field(default_factory=list)

    # Post-create command
    post_create_command: str | None = None

    # Container environment variables
    container_env: dict[str, str] = field(default_factory=dict)

    # Additional packages to install (paude.json format)
    packages: list[str] = field(default_factory=list)

    # Build arguments
    build_args: dict[str, str] = field(default_factory=dict)

    # venv isolation mode: "auto" (default), "none", or list of directory names
    venv: VenvMode = "auto"

    # pip install at build time: False (default), True (auto-detect), or custom command
    pip_install: PipInstallMode = False
