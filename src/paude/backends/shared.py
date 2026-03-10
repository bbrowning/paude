"""Shared utilities for paude backends."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.agents.base import AgentConfig

PAUDE_LABEL_AGENT = "paude.io/agent"
SQUID_BLOCKED_LOG_PATH = "/tmp/squid-blocked.log"  # noqa: S108


def config_file_basename(config_file_name: str) -> str:
    """Strip leading dot from config file name.

    Example: '.claude.json' -> 'claude.json'
    """
    return config_file_name.lstrip(".")


def build_agent_env(config: AgentConfig) -> dict[str, str]:
    """Build agent env vars for container entrypoint parameterization."""
    env: dict[str, str] = {
        "PAUDE_AGENT_NAME": config.name,
        "PAUDE_AGENT_PROCESS": config.process_name,
        "PAUDE_AGENT_CONFIG_DIR": config.config_dir_name,
        "PAUDE_AGENT_INSTALL_SCRIPT": config.install_script,
        "PAUDE_AGENT_SESSION_NAME": config.session_name,
        "PAUDE_AGENT_LAUNCH_CMD": config.process_name,
    }
    env["PAUDE_AGENT_SEED_DIR"] = f"/tmp/{config.name}.seed"  # noqa: S108
    if config.config_file_name:
        basename = config_file_basename(config.config_file_name)
        env["PAUDE_AGENT_CONFIG_FILE"] = config.config_file_name
        env["PAUDE_AGENT_SEED_FILE"] = f"/tmp/{basename}.seed"  # noqa: S108
    else:
        env["PAUDE_AGENT_SEED_FILE"] = ""
    return env


def encode_path(path: Path, *, url_safe: bool = False) -> str:
    """Encode a path for storing in labels.

    Args:
        path: Path to encode.
        url_safe: Use URL-safe base64 encoding (for Podman labels).

    Returns:
        Base64-encoded path string.
    """
    encoder = base64.urlsafe_b64encode if url_safe else base64.b64encode
    return encoder(str(path).encode()).decode()


def decode_path(encoded: str, *, url_safe: bool = False) -> Path:
    """Decode a base64-encoded path.

    Args:
        encoded: Base64-encoded path string.
        url_safe: Use URL-safe base64 decoding (for Podman labels).

    Returns:
        Decoded Path object.
    """
    try:
        decoder = base64.urlsafe_b64decode if url_safe else base64.b64decode
        return Path(decoder(encoded.encode()).decode())
    except Exception:
        return Path(encoded)
