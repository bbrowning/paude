"""Configuration file parsing for paude."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paude.config.models import PaudeConfig
from paude.config.user_config import _string_list, _warn_unknown_keys


class ConfigError(Exception):
    """Error parsing configuration file."""


def parse_config(config_file: Path) -> PaudeConfig:
    """Parse a configuration file.

    Args:
        config_file: Path to a paude.json file.

    Returns:
        Parsed configuration.

    Raises:
        ConfigError: If the file cannot be parsed.
    """
    if config_file.name != "paude.json":
        raise ConfigError(f"Unknown config file type: {config_file}")

    try:
        content = config_file.read_text()
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {config_file}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Cannot read {config_file}: {e}") from e

    return _parse_paude_json(config_file, data)


def _extract_build_config(
    config_dir: Path, data: dict[str, Any]
) -> tuple[Path | None, Path | None, dict[str, str]]:
    """Extract dockerfile, build context, and build args from config data.

    Args:
        config_dir: Directory containing the config file.
        data: Parsed JSON data (must contain optional "build" key).

    Returns:
        Tuple of (dockerfile, build_context, build_args).
    """
    build_config = data.get("build", {})
    dockerfile: Path | None = None
    build_context: Path | None = None

    if "dockerfile" in build_config:
        dockerfile_path = build_config["dockerfile"]
        if not Path(dockerfile_path).is_absolute():
            dockerfile = config_dir / dockerfile_path
        else:
            dockerfile = Path(dockerfile_path)

    if "context" in build_config:
        context_path = build_config["context"]
        if not Path(context_path).is_absolute():
            build_context = config_dir / context_path
        else:
            build_context = Path(context_path)
        if build_context.exists():
            build_context = build_context.resolve()
    elif dockerfile:
        build_context = config_dir

    build_args = build_config.get("args", {})
    return dockerfile, build_context, build_args


def _parse_paude_json(config_file: Path, data: dict[str, Any]) -> PaudeConfig:
    """Parse a paude.json file.

    Args:
        config_file: Path to the config file.
        data: Parsed JSON data.

    Returns:
        Parsed configuration.
    """
    config_dir = config_file.parent

    base_image = data.get("base")
    packages = data.get("packages", [])
    setup_command = data.get("setup")

    # Extract build config
    dockerfile, build_context, build_args = _extract_build_config(config_dir, data)

    if "pip_install" in data:
        print(
            "Warning: 'pip_install' is deprecated and ignored.",
            file=sys.stderr,
        )
        print(
            "  → Use 'paude remote add --push' to sync, then install manually.",
            file=sys.stderr,
        )

    # Parse "create" section for create hints
    create_hints = _parse_create_section(data.get("create", {}))

    return PaudeConfig(
        config_file=config_file,
        config_type="paude",
        base_image=base_image,
        dockerfile=dockerfile,
        build_context=build_context,
        build_args=build_args,
        packages=packages,
        setup_command=setup_command,
        create_allowed_domains=create_hints.allowed_domains,
        create_agent=create_hints.agent,
        create_provider=create_hints.provider,
        create_agents=create_hints.agents,
        create_providers=create_hints.providers,
        create_agent_providers=create_hints.agent_providers,
        create_otel_endpoint=create_hints.otel_endpoint,
    )


_KNOWN_CREATE_KEYS = {
    "allowed-domains",
    "agent",
    "provider",
    "agents",
    "providers",
    "agent-providers",
    "otel-endpoint",
}


@dataclass
class CreateHints:
    """Parsed create hints from a project config 'create' section."""

    allowed_domains: list[str] = field(default_factory=list)
    agent: str | None = None
    provider: str | None = None
    agents: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    agent_providers: dict[str, str] = field(default_factory=dict)
    otel_endpoint: str | None = None


def _parse_create_section(create_data: dict[str, Any]) -> CreateHints:
    """Parse the 'create' section from project config.

    Args:
        create_data: The parsed "create" object (may be empty).

    Returns:
        Parsed CreateHints. Unknown or wrongly-typed values are dropped.
    """
    if not isinstance(create_data, dict):
        return CreateHints()

    _warn_unknown_keys(create_data, _KNOWN_CREATE_KEYS, "create section")

    allowed_domains = _string_list(create_data.get("allowed-domains", []))
    agents = _string_list(create_data.get("agents", []))
    providers = _string_list(create_data.get("providers", []))
    raw_agent_providers = create_data.get("agent-providers", {})
    agent_providers = (
        {
            key: value
            for key, value in raw_agent_providers.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_agent_providers, dict)
        else {}
    )

    agent = create_data.get("agent")
    if agent is not None and not isinstance(agent, str):
        agent = None

    provider = create_data.get("provider")
    if provider is not None and not isinstance(provider, str):
        provider = None

    otel_endpoint = create_data.get("otel-endpoint")
    if otel_endpoint is not None and not isinstance(otel_endpoint, str):
        otel_endpoint = None

    return CreateHints(
        allowed_domains=allowed_domains,
        agent=agent,
        provider=provider,
        agents=agents,
        providers=providers,
        agent_providers=agent_providers,
        otel_endpoint=otel_endpoint,
    )
