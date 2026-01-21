"""Configuration file parsing for paude."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from paude.config.models import FeatureSpec, PaudeConfig


class ConfigError(Exception):
    """Error parsing configuration file."""


def parse_config(config_file: Path) -> PaudeConfig:
    """Parse a configuration file.

    Args:
        config_file: Path to the config file (devcontainer.json or paude.json).

    Returns:
        Parsed configuration.

    Raises:
        ConfigError: If the file cannot be parsed.
    """
    try:
        content = config_file.read_text()
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {config_file}: {e}") from e
    except OSError as e:
        raise ConfigError(f"Cannot read {config_file}: {e}") from e

    # Determine config type
    if "devcontainer" in config_file.name or config_file.parent.name == ".devcontainer":
        return _parse_devcontainer(config_file, data)
    elif config_file.name == "paude.json":
        return _parse_paude_json(config_file, data)
    else:
        raise ConfigError(f"Unknown config file type: {config_file}")


def _parse_devcontainer(config_file: Path, data: dict[str, Any]) -> PaudeConfig:
    """Parse a devcontainer.json file.

    Args:
        config_file: Path to the config file.
        data: Parsed JSON data.

    Returns:
        Parsed configuration.
    """
    config_dir = config_file.parent

    # Extract image
    base_image = data.get("image")

    # Extract dockerfile path
    dockerfile: Path | None = None
    build_context: Path | None = None

    build_config = data.get("build", {})
    if "dockerfile" in build_config:
        dockerfile_path = build_config["dockerfile"]
        if not Path(dockerfile_path).is_absolute():
            dockerfile = config_dir / dockerfile_path
        else:
            dockerfile = Path(dockerfile_path)

    # Extract build context
    if "context" in build_config:
        context_path = build_config["context"]
        if not Path(context_path).is_absolute():
            build_context = config_dir / context_path
        else:
            build_context = Path(context_path)
        # Normalize the path
        if build_context.exists():
            build_context = build_context.resolve()
    elif dockerfile:
        # Default context is config directory
        build_context = config_dir

    # Extract build args
    build_args = build_config.get("args", {})

    # Parse features
    features: list[FeatureSpec] = []
    if "features" in data:
        for feature_url, options in data["features"].items():
            if isinstance(options, dict):
                features.append(FeatureSpec(url=feature_url, options=options))
            else:
                features.append(FeatureSpec(url=feature_url, options={}))
        if features:
            print(f"Found {len(features)} feature(s)", file=sys.stderr)

    # Parse postCreateCommand
    post_create_command: str | None = None
    if "postCreateCommand" in data:
        pcc = data["postCreateCommand"]
        if isinstance(pcc, list):
            post_create_command = " && ".join(pcc)
        else:
            post_create_command = pcc

    # Parse containerEnv
    container_env = data.get("containerEnv", {})

    # Warn about unsupported properties
    _warn_unsupported_properties(data)

    return PaudeConfig(
        config_file=config_file,
        config_type="devcontainer",
        base_image=base_image,
        dockerfile=dockerfile,
        build_context=build_context,
        features=features,
        post_create_command=post_create_command,
        container_env=container_env,
        build_args=build_args,
    )


def _parse_paude_json(config_file: Path, data: dict[str, Any]) -> PaudeConfig:
    """Parse a paude.json file.

    Args:
        config_file: Path to the config file.
        data: Parsed JSON data.

    Returns:
        Parsed configuration.

    Raises:
        ConfigError: If venv config is invalid.
    """
    base_image = data.get("base")
    packages = data.get("packages", [])
    setup_command = data.get("setup")

    venv_config = data.get("venv", "auto")
    if venv_config not in ("auto", "none") and not isinstance(venv_config, list):
        raise ConfigError(
            "Invalid venv config: expected 'auto', 'none', or list of directories"
        )
    if isinstance(venv_config, list):
        for item in venv_config:
            if not isinstance(item, str):
                raise ConfigError(
                    "Invalid venv config: list items must be strings"
                )

    pip_install = data.get("pip_install", False)
    if not isinstance(pip_install, (bool, str)):
        raise ConfigError(
            "Invalid pip_install config: expected boolean or string command"
        )

    return PaudeConfig(
        config_file=config_file,
        config_type="paude",
        base_image=base_image,
        packages=packages,
        post_create_command=setup_command,
        venv=venv_config,
        pip_install=pip_install,
    )


def _warn_unsupported_properties(data: dict[str, Any]) -> None:
    """Warn about unsupported properties in devcontainer.json."""
    unsupported = [
        "mounts", "runArgs", "privileged", "capAdd", "forwardPorts", "remoteUser"
    ]
    for prop in unsupported:
        if prop in data:
            print(
                f"Warning: Ignoring unsupported property '{prop}' in config",
                file=sys.stderr,
            )
            print("  → paude controls this for security", file=sys.stderr)
