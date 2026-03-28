"""OpenClaw agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import AgentConfig, build_environment_from_config
from paude.mounts import resolve_path

_OPENCLAW_SECRET_VARS = [
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
]

_OPENCLAW_PASSTHROUGH_VARS = [
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_PROJECT_ID",
    "GOOGLE_CLOUD_LOCATION",
    "CLOUD_ML_REGION",
]

_OPENCLAW_PASSTHROUGH_PREFIXES = [
    "CLOUDSDK_AUTH_",
]


class OpenClawAgent:
    """OpenClaw agent implementation.

    OpenClaw is a web-based AI assistant gateway (port 18789).
    Unlike CLI agents, the user interacts via a browser.
    The tmux session shows server logs.
    """

    def __init__(self) -> None:
        self._config = AgentConfig(
            name="openclaw",
            display_name="OpenClaw",
            process_name="node",
            session_name="openclaw",
            install_script="",
            install_dir=".local/bin",
            env_vars={
                "NODE_USE_ENV_PROXY": "1",
            },
            passthrough_env_vars=list(_OPENCLAW_PASSTHROUGH_VARS),
            secret_env_vars=list(_OPENCLAW_SECRET_VARS),
            passthrough_env_prefixes=list(_OPENCLAW_PASSTHROUGH_PREFIXES),
            config_dir_name=".openclaw",
            config_file_name=None,
            config_excludes=[],
            config_sync_files_only=[],
            activity_files=[],
            yolo_flag=None,
            clear_command=None,
            extra_domain_aliases=["openclaw"],
            exposed_ports=[(18789, 18789)],
            default_base_image="ghcr.io/openclaw/openclaw:latest",
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        """Return Dockerfile lines for OpenClaw.

        When using the official OpenClaw image as base, the agent is
        pre-installed. We just ensure the launch script is accessible.
        When using a different base, install Node.js and OpenClaw via npm.
        """
        return [
            "",
            "# OpenClaw: detect if pre-installed or install from npm",
            "USER root",
            "RUN if command -v openclaw >/dev/null 2>&1; then"
            "  echo 'OpenClaw already installed';"
            " elif command -v node >/dev/null 2>&1; then"
            "  npm install -g openclaw;"
            " else"
            "  if command -v apt-get >/dev/null 2>&1; then"
            "    apt-get update && apt-get install -y --no-install-recommends"
            " nodejs npm && rm -rf /var/lib/apt/lists/*;"
            "  elif command -v dnf >/dev/null 2>&1; then"
            "    dnf install -y nodejs npm && dnf clean all;"
            "  fi;"
            "  npm install -g openclaw;"
            " fi",
            "",
            "# Ensure Node.js respects http_proxy/https_proxy env vars",
            "ENV NODE_USE_ENV_PROXY=1",
            "",
            "# Patch web_fetch to respect proxy env vars",
            ("RUN /usr/local/bin/patch-proxy-fetch.sh --force 2>&1 || true"),
            "USER paude",
            f"WORKDIR {container_home}",
        ]

    def apply_sandbox_config(self, home: str, workspace: str, args: str) -> str:
        """Return shell script to pre-configure OpenClaw for containerized use.

        API keys are NOT written here. They arrive securely via
        /credentials/env/ and are loaded by the entrypoint into the
        process environment before the agent launches.
        """
        return f"""\
#!/bin/bash
# Pre-configure OpenClaw for containerized operation
config_dir="{home}/.openclaw"
mkdir -p "$config_dir" 2>/dev/null || true

# Write gateway configuration if not already present
config_file="$config_dir/openclaw.json"
if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<OCCONFIG
{{
  "gateway": {{
    "port": 18789,
    "bind": "lan"
  }},
  "agents": {{
    "defaults": {{
      "workspace": "{workspace}",
      "model": {{
        "primary": "anthropic-vertex/claude-opus-4-6"
      }}
    }}
  }}
}}
OCCONFIG
    chmod g+rw "$config_file" 2>/dev/null || true
fi
"""

    def launch_command(self, args: str) -> str:
        cmd = "openclaw gateway --allow-unconfigured"
        if args:
            return f"{cmd} {args}"
        return cmd

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        config_dir = home / ".openclaw"
        resolved = resolve_path(config_dir)
        if resolved and resolved.is_dir():
            mounts.extend(["-v", f"{resolved}:/tmp/openclaw.seed:ro"])

        return mounts

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
