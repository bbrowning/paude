"""Cursor CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import AgentConfig, build_environment_from_config
from paude.mounts import resolve_path

_CURSOR_SECRET_VARS = [
    "CURSOR_API_KEY",
]


class CursorAgent:
    """Cursor CLI agent implementation."""

    def __init__(self) -> None:
        self._config = AgentConfig(
            name="cursor",
            display_name="Cursor",
            process_name="agent",
            session_name="cursor",
            install_script="curl https://cursor.com/install -fsS | bash",
            install_dir=".local/bin",
            env_vars={
                "APPIMAGE_EXTRACT_AND_RUN": "1",
                "NODE_USE_ENV_PROXY": "1",
            },
            passthrough_env_vars=[],
            secret_env_vars=list(_CURSOR_SECRET_VARS),
            passthrough_env_prefixes=[],
            config_dir_name=".cursor",
            config_file_name=None,
            config_excludes=[],
            config_sync_files_only=["cli-config.json"],
            activity_files=[],
            yolo_flag="--yolo",
            clear_command="/clear",
            extra_domain_aliases=["cursor"],
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        lines = [
            "",
            "# Install Cursor CLI",
            "USER paude",
            f"WORKDIR {container_home}",
            f"RUN umask 0002 && {self._config.install_script}",
            "",
            "# Allow AppImage to run without FUSE in containers",
            "ENV APPIMAGE_EXTRACT_AND_RUN=1",
            "",
            "# Ensure Node.js respects http_proxy/https_proxy env vars",
            "ENV NODE_USE_ENV_PROXY=1",
            "",
            "# Ensure agent is in PATH",
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]
        return lines

    def apply_sandbox_config(self, home: str, workspace: str, args: str) -> str:
        return f"""\
#!/bin/bash
# Pre-configure Cursor CLI to suppress onboarding prompts
cli_config="{home}/.cursor/cli-config.json"
mkdir -p "{home}/.cursor" 2>/dev/null || true

# Seed from host cli-config.json if available (carries auth tokens)
if [ -f /tmp/cursor-cli-config.seed ]; then
    cp /tmp/cursor-cli-config.seed "$cli_config"
    chmod g+rw "$cli_config" 2>/dev/null || true
fi

# Ensure version field exists so CLI doesn't prompt for setup,
# and force HTTP/1.1 for agent inference (HTTP/2 bypasses proxy).
if [ -f "$cli_config" ]; then
    jq '. * {{"version": (.version // 1), "network": {{"useHttp1ForAgent": true}}}}' \
        "$cli_config" > "${{cli_config}}.tmp" \
        && mv "${{cli_config}}.tmp" "$cli_config"
else
    jq -n '{{"version": 1, "network": {{"useHttp1ForAgent": true}}}}' > "$cli_config"
fi

# Sync Cursor auth.json (accessToken/refreshToken) from host
mkdir -p "{home}/.config/cursor" 2>/dev/null || true
# Podman path: seed file bind-mounted at /tmp/
if [ -f /tmp/cursor-auth.seed ]; then
    cp /tmp/cursor-auth.seed "{home}/.config/cursor/auth.json"
    chmod g+rw "{home}/.config/cursor/auth.json" 2>/dev/null || true
fi
# OpenShift path: synced to /credentials/ by sync.py
if [ -f /credentials/cursor-auth.json ]; then
    cp /credentials/cursor-auth.json "{home}/.config/cursor/auth.json"
    chmod g+rw "{home}/.config/cursor/auth.json" 2>/dev/null || true
fi

# Pre-trust workspace folder so Cursor doesn't prompt on every connect
ws_slug="${{{workspace}//\\//-}}"
ws_slug="${{ws_slug#-}}"
trusted_dir="{home}/.cursor/projects/$ws_slug"
mkdir -p "$trusted_dir" 2>/dev/null || true
cat > "$trusted_dir/.workspace-trusted" <<TRUST
{{
  "trustedAt": "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)",
  "workspacePath": "{workspace}"
}}
TRUST
"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"agent {args}"
        return "agent"

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        # IMPORTANT: Only mount cli-config.json, NEVER the entire .cursor directory.
        # ~/.cursor contains Cursor IDE data (extensions/, worktrees/, etc.) that
        # can exceed 1 GB and 26k+ files. Only cli-config.json is needed for CLI
        # auth tokens from `agent login`.
        cli_config = home / ".cursor" / "cli-config.json"
        resolved = resolve_path(cli_config)
        if resolved and resolved.is_file():
            mounts.extend(["-v", f"{resolved}:/tmp/cursor-cli-config.seed:ro"])

        # Mount auth.json (accessToken/refreshToken) from ~/.config/cursor/
        auth_json = home / ".config" / "cursor" / "auth.json"
        resolved_auth = resolve_path(auth_json)
        if resolved_auth and resolved_auth.is_file():
            mounts.extend(["-v", f"{resolved_auth}:/tmp/cursor-auth.seed:ro"])

        return mounts

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
