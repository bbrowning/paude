"""Configuration data models for paude."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class PaudeConfig:
    """Configuration for a paude workspace.

    This dataclass represents the parsed configuration from paude.json or no
    config (defaults).
    """

    config_file: Path | None = None
    config_type: Literal["default", "paude"] = "default"

    # Image configuration (mutually exclusive with dockerfile)
    base_image: str | None = None

    # Dockerfile configuration (mutually exclusive with base_image)
    dockerfile: Path | None = None
    build_context: Path | None = None

    # Setup command
    setup_command: str | None = None

    # Additional packages to install (paude.json format)
    packages: list[str] = field(default_factory=list)

    # Build arguments
    build_args: dict[str, str] = field(default_factory=dict)

    # Create hints (from paude.json "create" section)
    create_allowed_domains: list[str] = field(default_factory=list)
    create_agent: str | None = None
    create_provider: str | None = None
    create_agents: list[str] = field(default_factory=list)
    create_providers: list[str] = field(default_factory=list)
    create_agent_providers: dict[str, str] = field(default_factory=dict)
    create_otel_endpoint: str | None = None

    @property
    def has_customizations(self) -> bool:
        """Whether this config requires a custom image build."""
        return bool(
            self.base_image or self.dockerfile or self.packages or self.setup_command
        )
