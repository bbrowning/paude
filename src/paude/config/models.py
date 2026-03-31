"""Configuration data models for paude."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


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

    # Secret environment variable mapping (container_name -> host_name).
    # Values read from host os.environ, injected securely via tmpfs/exec,
    # never in container spec. Supports both list (same name) and dict (rename).
    container_secret_env: dict[str, str] = field(default_factory=dict)

    # Additional packages to install (paude.json format)
    packages: list[str] = field(default_factory=list)

    # Build arguments
    build_args: dict[str, str] = field(default_factory=dict)

    # Create hints (from paude.json "create" section)
    create_allowed_domains: list[str] = field(default_factory=list)
    create_agent: str | None = None
    create_provider: str | None = None
    create_otel_endpoint: str | None = None

    @property
    def has_customizations(self) -> bool:
        """Whether this config requires a custom image build."""
        return bool(
            self.base_image
            or self.dockerfile
            or self.packages
            or self.features
            or self.post_create_command
        )
