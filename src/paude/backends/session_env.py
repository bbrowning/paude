"""Session environment building and path encoding for paude backends."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentConfig
    from paude.backends.base import SessionConfig


def enrich_port_url(url: str, token: str | None) -> str:
    """Append an auth token fragment to a URL if a token is available."""
    return f"{url}/#token={token}" if token else url


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
    if config.config_file_name:
        env["PAUDE_AGENT_CONFIG_FILE"] = config.config_file_name
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


def build_session_env(
    config: SessionConfig,
    agent: Agent,
    proxy_name: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Build environment variables and args for a session.

    Builds agent env, YOLO flags, agent args, proxy env, and prompt suppression.

    When a proxy is configured, all real credentials are handled by the proxy
    container and the agent only sees dummy sentinel values.

    Args:
        config: Session configuration.
        agent: Resolved agent instance.
        proxy_name: Proxy container/service name (None if no proxy).

    Returns:
        Tuple of (env_dict, agent_args).
    """
    env = dict(config.env)
    env.update(build_agent_env(agent.config))
    # build_agent_env sets LAUNCH_CMD to process_name, which is wrong for
    # agents where the launch binary differs from the process name (e.g.
    # OpenClaw: process_name="node" but launch is "openclaw gateway ...").
    # Override with the agent's actual launch command (no args — those are
    # passed separately via PAUDE_AGENT_ARGS).
    env["PAUDE_AGENT_LAUNCH_CMD"] = agent.launch_command("")

    agent_args = list(config.args)
    if config.yolo and agent.config.yolo_flag:
        agent_args = [agent.config.yolo_flag] + agent_args

    if agent_args:
        env[agent.config.args_env_var] = " ".join(agent_args)
    # Backward compat: also set PAUDE_CLAUDE_ARGS for existing containers
    if agent_args and agent.config.name == "claude":
        env["PAUDE_CLAUDE_ARGS"] = " ".join(agent_args)

    env["PAUDE_SUPPRESS_PROMPTS"] = "1"

    if proxy_name:
        from paude.backends.proxy_config import PROXY_MANAGED_CREDENTIAL
        from paude.environment import build_proxy_environment

        env.update(build_proxy_environment(proxy_name))
        for var in agent.config.secret_env_vars:
            env[var] = PROXY_MANAGED_CREDENTIAL
        env["GH_TOKEN"] = PROXY_MANAGED_CREDENTIAL

    return env, agent_args


def generate_sandbox_config_script(
    agent_name: str,
    workspace: str,
    args: str,
    provider: str | None = None,
    *,
    yolo: bool = False,
) -> str:
    """Generate the sandbox config bash script for an agent."""
    from paude.agents import get_agent
    from paude.constants import CONTAINER_HOME

    agent = get_agent(agent_name, provider=provider)
    return agent.apply_sandbox_config(CONTAINER_HOME, workspace, args, yolo=yolo)
