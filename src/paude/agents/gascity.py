"""Gas City multi-agent orchestration agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    nodejs_prereq_install_lines,
)

GC_VERSION = "1.4.0"
DOLT_VERSION = "2.1.10"
BD_VERSION = "1.1.0"


class GascityAgent:
    """Gas City agent — composite agent with gc, Claude Code, and Gemini CLI.

    Gas City bundles the Claude Code and Gemini CLI toolchains (see
    ``bundled_agents``); those installs are contributed by ClaudeAgent and
    GeminiAgent. The image build path (the Dockerfile generators in
    ``paude.config``) calls ``dockerfile_install_lines_for_agent()``, which
    stitches every bundled toolchain into the image, so this agent's own
    Dockerfile lines cover only the Gas City core: gc, dolt, bd, and the Node.js
    prerequisite.
    """

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("gascity", provider)
        creds.extra_env_vars["NODE_USE_ENV_PROXY"] = "1"
        creds.extra_env_vars["BD_DOLT_AUTO_COMMIT"] = "off"
        creds.extra_env_vars["BD_EXPORT_AUTO"] = "false"
        creds.extra_env_vars["DO_NOT_TRACK"] = "1"
        creds.extra_env_vars["GC_DISABLE_USAGE_METRICS"] = "1"
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
            config_dir_name=".gc",
            config_file_name=None,
            yolo_flag=None,
            clear_command=None,
            # Child-agent domains are contributed by the resolved
            # composition. Gas City itself has no runtime domain alias.
            extra_domain_aliases=[],
            provider=creds.resolved_provider_name,
            bundled_agents=["claude", "gemini"],
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        install_dir = f"{container_home}/.local/bin"

        lines = [
            "",
            "# --- Gas City core install (gc, dolt, bd) ---",
            "",
            "# Node.js runtime (shared prereq; deduped with the bundled Gemini CLI)",
            *nodejs_prereq_install_lines(),
            "",
            "# Install flock (util-linux) and lsof",
            "RUN dnf install -y util-linux lsof && dnf clean all",
            "",
            "# Install dolt, bd (beads), and gc (Gas City)",
            "USER paude",
            f"WORKDIR {container_home}",
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
            '.tar.gz" | tar xz -C $D gc && '
            "$D/dolt config --global --set metrics.disabled true && "
            '$D/dolt config --global --set user.name "Paude Agent" && '
            '$D/dolt config --global --set user.email "agent@paude.local" && '
            f"chmod -R g+rwX {container_home}/.dolt",
            "",
            f'ENV PATH="{install_dir}:$PATH"',
        ]
        return lines

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        return "#!/bin/bash\n" + self._dolt_identity_script()

    @staticmethod
    def _dolt_identity_script() -> str:
        return (
            "GIT_NAME=$(git config --global user.name 2>/dev/null)\n"
            "GIT_EMAIL=$(git config --global user.email 2>/dev/null)\n"
            'if [ -n "$GIT_NAME" ]; then '
            'dolt config --global --set user.name "$GIT_NAME" > /dev/null 2>&1; '
            "fi\n"
            'if [ -n "$GIT_EMAIL" ]; then '
            'dolt config --global --set user.email "$GIT_EMAIL" > /dev/null 2>&1; '
            "fi\n"
        )

    def launch_command(self, args: str) -> str:
        return "bash"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
