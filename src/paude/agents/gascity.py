"""Gas City multi-agent orchestration agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    claude_trust_script,
    gemini_trust_script,
    pipefail_install_lines,
)

GC_VERSION = "1.1.0"
DOLT_VERSION = "1.88.0"
BD_VERSION = "1.0.4"

_CLAUDE_INSTALL_SCRIPT = "curl -fsSL https://claude.ai/install.sh | bash"

_CLAUDE_CONFIG = AgentConfig(
    name="claude",
    display_name="Claude Code",
    process_name="claude",
    session_name="claude",
    install_script=_CLAUDE_INSTALL_SCRIPT,
)


class GascityAgent:
    """Gas City agent — composite agent with gc, Claude Code, and Gemini CLI."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("gascity", provider)
        creds.extra_env_vars["NODE_USE_ENV_PROXY"] = "1"
        self._config = AgentConfig(
            name="gascity",
            display_name="Gas City",
            process_name="gc",
            session_name="gascity",
            install_script="echo 'gc pre-installed at build time'",
            env_vars=creds.extra_env_vars,
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".gascity",
            config_file_name=None,
            yolo_flag=None,
            clear_command=None,
            extra_domain_aliases=[
                "gascity",
                "claude",
                "gemini",
                "nodejs",
            ],
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        install_dir = f"{container_home}/.local/bin"

        claude_lines = pipefail_install_lines(
            _CLAUDE_CONFIG,
            container_home,
        )
        claude_lines[1] += f" && rm -f {container_home}/.claude.json"

        lines = [
            "",
            "# --- Gas City composite agent install ---",
            "",
            "# Install Node.js, Gemini CLI, and flock",
            "USER root",
            "RUN dnf install -y nodejs npm util-linux lsof && dnf clean all",
            "",
            "# Install Gemini CLI and patch OTEL proxy",
            "RUN npm install -g @google/gemini-cli"
            " && /usr/local/bin/patch-gemini-otel-proxy.sh"
            " --force 2>&1",
            "",
            "# Install Claude Code",
            "USER paude",
            f"WORKDIR {container_home}",
            *claude_lines,
            "",
            "# Install dolt, bd (beads), and gc (Gas City)",
            f"RUN mkdir -p {install_dir} && "
            f"D={install_dir} && "
            "ARCH=$(uname -m) && "
            'case "$ARCH" in '
            'x86_64) BIN_ARCH="amd64" ;; '
            'aarch64) BIN_ARCH="arm64" ;; '
            '*) echo "Unsupported: $ARCH" && exit 1 ;; '
            "esac && "
            'curl -fsSL "https://github.com/dolthub/dolt'
            f"/releases/download/v{DOLT_VERSION}"
            '/dolt-linux-${BIN_ARCH}.tar.gz"'
            " | tar xz --strip-components=2"
            " -C $D dolt-linux-${BIN_ARCH}/bin/dolt && "
            'curl -fsSL "https://github.com/gastownhall'
            f"/beads/releases/download/v{BD_VERSION}"
            f'/beads_{BD_VERSION}_linux_${{BIN_ARCH}}.tar.gz"'
            " | tar xz -C $D bd && "
            'curl -fsSL "https://github.com/gastownhall'
            f"/gascity/releases/download/v{GC_VERSION}"
            f"/gascity_{GC_VERSION}_linux_${{BIN_ARCH}}"
            '.tar.gz" | tar xz -C $D gc',
            "",
            f'ENV PATH="{install_dir}:$PATH"',
        ]
        return lines

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return (
            "#!/bin/bash\n"
            + claude_trust_script(home, workspace)
            + gemini_trust_script(home, workspace)
        )

    def launch_command(self, args: str) -> str:
        return "bash"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
