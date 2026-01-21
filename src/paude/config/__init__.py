"""Configuration detection and parsing for paude."""

from paude.config.detector import detect_config
from paude.config.dockerfile import generate_workspace_dockerfile
from paude.config.models import FeatureSpec, PaudeConfig, PipInstallMode, VenvMode
from paude.config.parser import ConfigError, parse_config

__all__ = [
    "ConfigError",
    "FeatureSpec",
    "PaudeConfig",
    "PipInstallMode",
    "VenvMode",
    "detect_config",
    "generate_workspace_dockerfile",
    "parse_config",
]
