"""Base protocol and data types for agent abstraction."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class AgentConfig:
    """Configuration for a CLI coding agent.

    Attributes:
        name: Agent identifier (e.g., "claude", "gemini", "codex").
        display_name: Human-readable name (e.g., "Claude Code").
        process_name: Process name for pgrep (e.g., "claude").
        session_name: Tmux session name (e.g., "claude").
        install_script: Shell command to install the agent.
        install_dir: Relative to HOME (e.g., ".local/bin").
        env_vars: Agent-specific environment variables.
        skip_install_env_var: Env var to skip installation.
        passthrough_env_vars: Host env vars to forward to container.
        secret_env_vars: Host env vars to deliver securely (not in container spec).
        passthrough_env_prefixes: Host env var prefixes to forward.
        config_dir_name: Config directory under HOME (e.g., ".claude").
        config_file_name: Config file under HOME (e.g., ".claude.json"), or None.
        activity_files: Paths (relative to config dir) for activity detection.
        yolo_flag: CLI flag to skip permissions
            (e.g., "--dangerously-skip-permissions").
        clear_command: Tmux command to reset conversation (e.g., "/clear").
        args_env_var: Env var name for passing agent args.
        exposed_ports: Ports this agent needs exposed as (host, container) tuples.
            Empty for CLI agents; used by web-based agents like OpenClaw.
        default_base_image: Default container base image for this agent, or None
            to use paude's standard base image.
    """

    name: str
    display_name: str
    process_name: str
    session_name: str
    install_script: str
    install_dir: str = ".local/bin"
    env_vars: dict[str, str] = field(default_factory=dict)
    skip_install_env_var: str = "PAUDE_SKIP_AGENT_INSTALL"
    passthrough_env_vars: list[str] = field(default_factory=list)
    secret_env_vars: list[str] = field(default_factory=list)
    passthrough_env_prefixes: list[str] = field(default_factory=list)
    config_dir_name: str = ".claude"
    config_file_name: str | None = ".claude.json"
    activity_files: list[str] = field(default_factory=list)
    yolo_flag: str | None = "--dangerously-skip-permissions"
    clear_command: str | None = "/clear"
    args_env_var: str = "PAUDE_AGENT_ARGS"
    extra_domain_aliases: list[str] = field(default_factory=lambda: ["claude"])
    exposed_ports: list[tuple[int, int]] = field(default_factory=list)
    default_base_image: str | None = None
    provider: str | None = None


@dataclass
class ProviderCredentials:
    """Resolved provider credentials for an agent."""

    passthrough_env_vars: list[str] = field(default_factory=list)
    secret_env_vars: list[str] = field(default_factory=list)
    passthrough_env_prefixes: list[str] = field(default_factory=list)
    extra_env_vars: dict[str, str] = field(default_factory=dict)
    resolved_provider_name: str = ""
    model_config: dict[str, str] = field(default_factory=dict)


def build_provider_credentials(
    agent_name: str, provider: str | None
) -> ProviderCredentials:
    """Build credential lists from provider configuration."""
    from paude.providers.agent_providers import DEFAULT_PROVIDER, resolve_agent_provider

    resolved_name = (
        provider if provider is not None else DEFAULT_PROVIDER.get(agent_name)
    )
    if resolved_name is None:
        return ProviderCredentials()

    provider_config, agent_config = resolve_agent_provider(agent_name, resolved_name)

    passthrough = list(provider_config.passthrough_env_vars)
    passthrough.extend(agent_config.extra_passthrough_env_vars)

    secret = list(provider_config.secret_env_vars)
    secret.extend(agent_config.extra_secret_env_vars)

    prefixes = list(provider_config.passthrough_env_prefixes)

    extra_env = dict(agent_config.extra_env_vars)

    return ProviderCredentials(
        passthrough_env_vars=passthrough,
        secret_env_vars=secret,
        passthrough_env_prefixes=prefixes,
        extra_env_vars=extra_env,
        resolved_provider_name=resolved_name,
        model_config=dict(agent_config.model_config),
    )


def build_environment_from_config(config: AgentConfig) -> dict[str, str]:
    """Build environment dict from static env_vars and passthrough vars from os.environ.

    Secret env vars (listed in config.secret_env_vars) are excluded from
    this output. Use build_secret_environment_from_config() for those.
    """
    secret_set = set(config.secret_env_vars)
    env: dict[str, str] = {}
    env.update(config.env_vars)
    for var in config.passthrough_env_vars:
        if var in secret_set:
            continue
        value = os.environ.get(var)
        if value:
            env[var] = value
    for prefix in config.passthrough_env_prefixes:
        for key, value in os.environ.items():
            if key.startswith(prefix) and key not in secret_set:
                env[key] = value
    return env


def build_secret_environment_from_config(config: AgentConfig) -> dict[str, str]:
    """Build environment dict for secret env vars from os.environ."""
    env: dict[str, str] = {}
    for var in config.secret_env_vars:
        value = os.environ.get(var)
        if value:
            env[var] = value
    return env


def pipefail_install_lines(config: AgentConfig, container_home: str) -> list[str]:
    """Generate Dockerfile lines for a curl|bash install with pipefail and verification.

    Wraps the install in a bash pipefail SHELL so curl failures propagate,
    then verifies the binary exists. Resets SHELL afterward.
    """
    binary = f"{container_home}/{config.install_dir}/{config.process_name}"
    return [
        'SHELL ["/bin/bash", "-o", "pipefail", "-c"]',
        f"RUN umask 0002 && {config.install_script}"
        f' && test -x {binary} || (echo "ERROR: {config.display_name}'
        f' installation failed — binary not found at {binary}" && exit 1)',
        'SHELL ["/bin/sh", "-c"]',
    ]


class Agent(Protocol):
    """Protocol for CLI coding agent implementations."""

    @property
    def config(self) -> AgentConfig:
        """Return the agent configuration."""
        ...

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        """Return Dockerfile lines to install the agent.

        Args:
            container_home: Home directory path inside the container.

        Returns:
            List of Dockerfile instruction lines.
        """
        ...

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        """Return shell script content to apply sandbox config.

        This script suppresses interactive prompts inside the container.

        Args:
            home: Home directory inside container.
            workspace: Workspace directory inside container.
            args: Agent args string.

        Returns:
            Shell script content.
        """
        ...

    def launch_command(self, args: str) -> str:
        """Return the shell command to launch the agent.

        Args:
            args: Arguments to pass to the agent.

        Returns:
            Shell command string.
        """
        ...

    def host_config_mounts(self, home: Path) -> list[str]:
        """Return podman mount arguments for agent-specific config.

        Args:
            home: Host home directory.

        Returns:
            List of mount argument strings (e.g., ["-v", "src:dst:ro"]).
        """
        ...

    def build_environment(self) -> dict[str, str]:
        """Return agent-specific environment variables from host.

        Returns:
            Dictionary of environment variables to pass to the container.
        """
        ...
