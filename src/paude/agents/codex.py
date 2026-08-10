"""Codex CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    nodejs_prereq_install_lines,
    pipefail_install_lines,
)
from paude.constants import CONTAINER_HOME

_INSTALL_SCRIPT = (
    'mkdir -p "$HOME/.local/bin" && '
    "ARCH=$(uname -m) && "
    'case "$ARCH" in '
    'x86_64) CODEX_ARCH="x86_64-unknown-linux-musl" ;; '
    'aarch64) CODEX_ARCH="aarch64-unknown-linux-musl" ;; '
    '*) echo "Unsupported architecture: $ARCH" && exit 1 ;; '
    "esac && "
    "curl -fsSL "
    '"https://github.com/openai/codex/releases/latest/download/'
    'codex-${CODEX_ARCH}.tar.gz"'
    ' | tar xz -C "$HOME/.local/bin" "codex-${CODEX_ARCH}" && '
    'mv "$HOME/.local/bin/codex-${CODEX_ARCH}" "$HOME/.local/bin/codex" && '
    # Recent Codex releases spawn a companion "code-mode host" binary via an
    # absolute path next to the codex binary for every tool call. Install it
    # alongside codex from the same (latest) release so the versioned handshake
    # matches and tool calls work out of the box.
    "curl -fsSL "
    '"https://github.com/openai/codex/releases/latest/download/'
    'codex-code-mode-host-${CODEX_ARCH}.tar.gz"'
    ' | tar xz -C "$HOME/.local/bin" "codex-code-mode-host-${CODEX_ARCH}" && '
    'mv "$HOME/.local/bin/codex-code-mode-host-${CODEX_ARCH}" '
    '"$HOME/.local/bin/codex-code-mode-host" && '
    # The build only verifies the primary codex binary (pipefail_install_lines),
    # so verify the companion here too — a missing companion fails every tool
    # call at runtime, the exact failure this install is meant to prevent.
    'test -x "$HOME/.local/bin/codex-code-mode-host"'
)


class CodexAgent:
    """Codex CLI agent implementation."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("codex", provider)
        self._config = AgentConfig(
            name="codex",
            display_name="Codex CLI",
            process_name="codex",
            session_name="codex",
            install_script=_INSTALL_SCRIPT,
            install_dir=".local/bin",
            env_vars={"CODEX_HOME": f"{CONTAINER_HOME}/.codex", **creds.extra_env_vars},
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".codex",
            extra_persistent_dir_names=[".agents"],
            config_file_name=None,
            # auth.json holds proxy-synthesized (not real) tokens; strip it from
            # backups anyway so bundles never carry auth material.
            credential_file_names=[".codex/auth.json"],
            activity_files=[],
            yolo_flag="--dangerously-bypass-approvals-and-sandbox",
            clear_command=None,
            extra_domain_aliases=creds.chatgpt_domain_aliases,
            required_domain_aliases=creds.chatgpt_domain_aliases,
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install Node.js for Codex documentation tooling",
            *nodejs_prereq_install_lines(),
            "",
            "# Install Codex CLI",
            "USER paude",
            f"WORKDIR {container_home}",
            *pipefail_install_lines(self._config, container_home),
            "",
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return f'#!/bin/bash\nmkdir -p "{home}/.codex" 2>/dev/null || true\n'

    def launch_command(self, args: str) -> str:
        if args:
            return f"codex {args}"
        return "codex"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
