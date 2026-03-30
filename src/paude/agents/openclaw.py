"""OpenClaw agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
)
from paude.mounts import resolve_path


class OpenClawAgent:
    """OpenClaw agent implementation.

    OpenClaw is a web-based AI assistant gateway (port 18789).
    Unlike CLI agents, the user interacts via a browser.
    The tmux session shows server logs.
    """

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("openclaw", provider)
        creds.extra_env_vars["NODE_USE_ENV_PROXY"] = "1"
        self._model_config = creds.model_config

        self._config = AgentConfig(
            name="openclaw",
            display_name="OpenClaw",
            process_name="node",
            session_name="openclaw",
            install_script="",
            install_dir=".local/bin",
            env_vars=creds.extra_env_vars,
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
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
            provider=creds.resolved_provider_name,
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
            "",
            "# Patch OTEL SDK to route exports through HTTP proxy",
            ("RUN /usr/local/bin/patch-openclaw-otel-proxy.sh --force 2>&1 || true"),
            "",
            "# Patch OTEL log transport to bridge gateway and plugin-sdk modules",
            ("RUN /usr/local/bin/patch-openclaw-otel-logs.sh 2>&1 || true"),
            "",
            "# Install GitHub CLI (gh) via direct binary download",
            "ARG GH_VERSION=2.74.1",
            "RUN if ! command -v gh >/dev/null 2>&1; then"
            "  ARCH=$(uname -m) &&"
            '  case "$ARCH" in'
            '    x86_64) GH_ARCH="amd64" ;;'
            '    aarch64) GH_ARCH="arm64" ;;'
            '    *) echo "Unsupported architecture for gh: $ARCH" >&2 && exit 1 ;;'
            "  esac &&"
            "  curl -fsSL"
            ' "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${GH_ARCH}.tar.gz"'
            "  | tar xz -C /tmp &&"
            "  mv /tmp/gh_${GH_VERSION}_linux_${GH_ARCH}/bin/gh /usr/local/bin/gh &&"
            "  rm -rf /tmp/gh_${GH_VERSION}_linux_${GH_ARCH};"
            " fi",
            "USER paude",
            f"WORKDIR {container_home}",
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        """Return shell script to pre-configure OpenClaw for containerized use.

        API keys are NOT written here. They arrive securely via
        /credentials/env/ and are loaded by the entrypoint into the
        process environment before the agent launches.
        """
        primary_model = self._model_config.get(
            "primary", "anthropic-vertex/claude-opus-4-6"
        )
        exec_block = (
            '"host": "gateway", "security": "full", "ask": "off"'
            if yolo
            else (
                '"host": "gateway", "security": "allowlist", '
                '"ask": "on-miss", "strictInlineEval": true'
            )
        )
        tools_block = f"""\
"tools": {{
    "profile": "coding",
    "fs": {{ "workspaceOnly": true }},
    "exec": {{ {exec_block} }},
    "elevated": {{ "enabled": false }}
  }}"""
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
        "primary": "{primary_model}"
      }}
    }}
  }},
  {tools_block}
}}
OCCONFIG
    chmod g+rw "$config_file" 2>/dev/null || true
fi

# Enable diagnostics-otel plugin when OTEL endpoint is configured
if [ -n "${{OTEL_EXPORTER_OTLP_ENDPOINT:-}}" ]; then
    node -e '
const fs = require("fs");
const f = process.argv[1];
const endpoint = process.argv[2];
const protocol = process.argv[3];
let cfg = {{}};
try {{ cfg = JSON.parse(fs.readFileSync(f, "utf8")); }} catch(e) {{}}
if (!cfg.plugins) cfg.plugins = {{}};
if (!Array.isArray(cfg.plugins.allow)) cfg.plugins.allow = [];
const p = "diagnostics-otel";
if (!cfg.plugins.allow.includes(p)) cfg.plugins.allow.push(p);
if (!cfg.plugins.entries) cfg.plugins.entries = {{}};
cfg.plugins.entries[p] = {{ enabled: true }};
if (!cfg.diagnostics) cfg.diagnostics = {{}};
cfg.diagnostics.enabled = true;
cfg.diagnostics.otel = {{
  enabled: true,
  endpoint: endpoint,
  protocol: protocol,
  traces: true,
  metrics: true,
  logs: true
}};
fs.writeFileSync(f, JSON.stringify(cfg, null, 2) + "\\n");
' "$config_file" \
      "$OTEL_EXPORTER_OTLP_ENDPOINT" \
      "${{OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}}"
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
