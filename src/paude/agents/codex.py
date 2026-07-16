"""Codex CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    pipefail_install_lines,
)
from paude.constants import CONTAINER_HOME

CODEX_VERSION = "0.144.5"
CODEX_CHATGPT_PROFILE_NAME = "paude-chatgpt-http"
CODEX_CHATGPT_PROFILE_TARGET = (
    f"/home/paude/.codex/{CODEX_CHATGPT_PROFILE_NAME}.config.toml"
)
SYNTHETIC_CODEX_PROFILE_TOML = f'''model_provider = "{CODEX_CHATGPT_PROFILE_NAME}"

[model_providers.{CODEX_CHATGPT_PROFILE_NAME}]
name = "Paude ChatGPT HTTP"
base_url = "https://chatgpt.com/backend-api/codex"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
'''

_INSTALL_SCRIPT = (
    'mkdir -p "$HOME/.local/bin" && '
    "ARCH=$(uname -m) && "
    'case "$ARCH" in '
    'x86_64) CODEX_ARCH="x86_64-unknown-linux-musl" ;; '
    'aarch64) CODEX_ARCH="aarch64-unknown-linux-musl" ;; '
    '*) echo "Unsupported architecture: $ARCH" && exit 1 ;; '
    "esac && "
    "curl -fsSL "
    '"https://github.com/openai/codex/releases/download/'
    f"rust-v{CODEX_VERSION}"
    '/codex-${CODEX_ARCH}.tar.gz"'
    ' | tar xz -C "$HOME/.local/bin" "codex-${CODEX_ARCH}" && '
    'mv "$HOME/.local/bin/codex-${CODEX_ARCH}" "$HOME/.local/bin/codex"'
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
            config_file_name=None,
            activity_files=[],
            yolo_flag="--dangerously-bypass-approvals-and-sandbox",
            clear_command=None,
            extra_domain_aliases=["codex"],
            required_domain_aliases=["codex"],
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
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
