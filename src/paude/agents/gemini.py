"""Gemini CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    gemini_trust_script,
)


class GeminiAgent:
    """Gemini CLI agent implementation."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("gemini", provider)
        self._config = AgentConfig(
            name="gemini",
            display_name="Gemini CLI",
            process_name="gemini",
            session_name="gemini",
            # Runtime fallback only — requires Node.js already in the image.
            # Normal path: dockerfile_install_lines bakes Node.js + CLI into image,
            # and install_agent() skips via `command -v gemini`.
            install_script="npm install -g @google/gemini-cli@0.35.3",
            install_dir=".local/bin",
            env_vars=creds.extra_env_vars,
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".gemini",
            config_file_name=None,
            activity_files=[],
            yolo_flag="--yolo",
            clear_command="/clear",
            extra_domain_aliases=["gemini", "nodejs"],
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        lines = [
            "",
            "# Install Node.js for Gemini CLI",
            "USER root",
            "RUN dnf install -y nodejs npm && dnf clean all",
            "",
            "# Install Gemini CLI and patch OTEL proxy",
            "RUN npm install -g @google/gemini-cli@0.35.3"
            " && /usr/local/bin/patch-gemini-otel-proxy.sh"
            " --force 2>&1",
            "",
            "# Set up home directory",
            "USER paude",
            f"WORKDIR {container_home}",
        ]
        return lines

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return "#!/bin/bash\n" + gemini_trust_script(home, workspace)

    def launch_command(self, args: str) -> str:
        if args:
            return f"gemini {args}"
        return "gemini"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
