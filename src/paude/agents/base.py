"""Base protocol and data types for agent abstraction."""

from __future__ import annotations

import os
from collections.abc import Sequence
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
        bundled_agents: Names of other agent toolchains implied by this agent.
            When this agent is requested, each bundled agent is also expanded
            into the composed install set (e.g. gascity bundles claude and
            gemini).
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
    required_domain_aliases: list[str] = field(default_factory=list)
    exposed_ports: list[tuple[int, int]] = field(default_factory=list)
    default_base_image: str | None = None
    provider: str | None = None
    bundled_agents: list[str] = field(default_factory=list)


@dataclass
class ProviderCredentials:
    """Resolved provider credentials for an agent."""

    passthrough_env_vars: list[str] = field(default_factory=list)
    secret_env_vars: list[str] = field(default_factory=list)
    passthrough_env_prefixes: list[str] = field(default_factory=list)
    extra_env_vars: dict[str, str] = field(default_factory=dict)
    resolved_provider_name: str = ""
    model_config: dict[str, str] = field(default_factory=dict)

    @property
    def chatgpt_domain_aliases(self) -> list[str]:
        """Domain aliases needed when using the ChatGPT OAuth provider."""
        return ["chatgpt"] if self.resolved_provider_name == "chatgpt" else []


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
    _sync_equivalent_vars(env, config.passthrough_env_vars)
    return env


_EQUIVALENT_PAIRS: list[tuple[str, str]] = [
    ("GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION"),
    ("GOOGLE_CLOUD_LOCATION", "VERTEX_LOCATION"),
    ("GOOGLE_CLOUD_PROJECT", "ANTHROPIC_VERTEX_PROJECT_ID"),
]


def _sync_equivalent_vars(
    env: dict[str, str],
    passthrough_vars: list[str],
) -> None:
    """Ensure equivalent env vars are both set when either is present."""
    passthrough_set = set(passthrough_vars)
    for a, b in _EQUIVALENT_PAIRS:
        if a in passthrough_set and b in passthrough_set:
            if a in env and b not in env:
                env[b] = env[a]
            elif b in env and a not in env:
                env[a] = env[b]


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


def nodejs_prereq_install_lines() -> list[str]:
    """Return canonical Dockerfile lines to install the Node.js runtime as root.

    Emitted verbatim by every agent that needs Node.js (e.g. Gemini CLI, the
    Gas City core) so that compose_dockerfile_install_lines() can collapse the
    repeated install down to a single layer.
    """
    return [
        "USER root",
        "RUN dnf install -y nodejs npm && dnf clean all",
    ]


def claude_trust_script(home: str, workspace: str) -> str:
    """Generate shell snippet to suppress Claude Code trust/onboarding prompts."""
    return f"""\
claude_json="{home}/.claude.json"
if [ -f "$claude_json" ]; then
    jq --arg ws "{workspace}" '
        .hasCompletedOnboarding = true |
        .projects = {{($ws): {{hasTrustDialogAccepted: true}}}}
    ' "$claude_json" > "${{claude_json}}.tmp" \\
        && cp -f "${{claude_json}}.tmp" "$claude_json" \\
        && rm -f "${{claude_json}}.tmp"
else
    jq -n --arg ws "{workspace}" '{{
        hasCompletedOnboarding: true,
        projects: {{($ws): {{hasTrustDialogAccepted: true}}}}
    }}' > "$claude_json"
fi
chmod g+rw "$claude_json" 2>/dev/null || true
"""


def gemini_trust_script(home: str, workspace: str) -> str:
    """Generate shell snippet to pre-trust workspace for Gemini CLI."""
    return f"""\
trusted_json="{home}/.gemini/trustedFolders.json"
mkdir -p "{home}/.gemini" 2>/dev/null || true
if [ -f "$trusted_json" ]; then
    jq --arg ws "{workspace}" '. + {{($ws): "TRUST_FOLDER"}}' \\
        "$trusted_json" > "${{trusted_json}}.tmp" \\
        && mv "${{trusted_json}}.tmp" "$trusted_json"
else
    jq -n --arg ws "{workspace}" '{{($ws): "TRUST_FOLDER"}}' > "$trusted_json"
fi
"""


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


@dataclass(frozen=True)
class AgentComposition:
    """An ordered, deduplicated set of agents to install into one image.

    Attributes:
        primary: The primary agent — the first requested name. Drives launch
            behaviour, sandbox config, and the container's default agent.
        agents: The full install set in stable order — the primary plus every
            requested/bundled toolchain, deduplicated by name. The primary is
            always the first element.
    """

    primary: Agent
    agents: list[Agent]

    @property
    def names(self) -> list[str]:
        """Return the install-set agent names, in install order."""
        return [agent.config.name for agent in self.agents]


_PREREQ_INSTALL_MARKERS = (
    "dnf install",
    "yum install",
    "apt-get install",
    "apk add",
)


def _is_dedupable_prereq(line: str) -> bool:
    """Return True for OS-package install lines that are safe to deduplicate.

    Only ``RUN`` lines that invoke a package manager qualify. Tool installs
    (curl|bash, npm install of a specific CLI, tarball downloads) are never
    treated as shared prerequisites and are always preserved.
    """
    stripped = line.strip()
    if not stripped.startswith("RUN "):
        return False
    return any(marker in stripped for marker in _PREREQ_INSTALL_MARKERS)


def _strip_trailing_layout(lines: list[str]) -> list[str]:
    """Drop trailing blank/USER/WORKDIR lines so a canonical pair can be appended."""
    result = list(lines)
    while result:
        last = result[-1].strip()
        if last == "" or last.startswith("USER ") or last.startswith("WORKDIR "):
            result.pop()
        else:
            break
    return result


def compose_dockerfile_install_lines(
    agents: Sequence[Agent], container_home: str
) -> list[str]:
    """Concatenate multiple agents' Dockerfile install lines into one layer set.

    Agents are laid out in the given order. Shared prerequisite install lines
    (e.g. the Node.js/npm package install) emitted by more than one agent are
    deduplicated — the first occurrence wins and later identical package-install
    lines are dropped. The result always ends with a consistent
    ``USER paude`` / ``WORKDIR {container_home}`` pair, regardless of how each
    agent terminates its own lines.

    Args:
        agents: Agents to install, in the desired build order.
        container_home: Home directory path inside the container.

    Returns:
        Combined, deduplicated list of Dockerfile instruction lines.
    """
    combined: list[str] = []
    seen_prereqs: set[str] = set()
    for agent in agents:
        for line in agent.dockerfile_install_lines(container_home):
            if _is_dedupable_prereq(line):
                key = line.strip()
                if key in seen_prereqs:
                    continue
                seen_prereqs.add(key)
            combined.append(line)

    combined = _strip_trailing_layout(combined)
    combined.append("")
    combined.append("USER paude")
    combined.append(f"WORKDIR {container_home}")
    return combined
