"""Claude Code agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    pipefail_install_lines,
)
from paude.mounts import resolve_path

# Keep in sync with the case statement in copy_agent_config() in
# containers/paude/entrypoint-session.sh (defense-in-depth).
_CLAUDE_CONFIG_EXCLUDES = [
    "/backups",
    "/cache",
    "/debug",
    "/downloads",
    "/file-history",
    "/history.jsonl",
    "/paste-cache",
    "/plans",
    "/session-env",
    "/sessions",
    "/shell-snapshots",
    "/stats-cache.json",
    "/statsig",
    "/tasks",
    "/todos",
    "/projects",
    "/.git",
]

_CLAUDE_ACTIVITY_FILES = [
    "history.jsonl",
    "debug/*",
]


class ClaudeAgent:
    """Claude Code agent implementation."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("claude", provider)
        self._config = AgentConfig(
            name="claude",
            display_name="Claude Code",
            process_name="claude",
            session_name="claude",
            install_script="curl -fsSL https://claude.ai/install.sh | bash",
            install_dir=".local/bin",
            env_vars=creds.extra_env_vars,
            skip_install_env_var="PAUDE_SKIP_AGENT_INSTALL",
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".claude",
            config_file_name=".claude.json",
            config_excludes=list(_CLAUDE_CONFIG_EXCLUDES),
            activity_files=list(_CLAUDE_ACTIVITY_FILES),
            yolo_flag="--dangerously-skip-permissions",
            clear_command="/clear",
            args_env_var="PAUDE_AGENT_ARGS",
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        install_lines = pipefail_install_lines(self._config, container_home)
        # Remove the generated .claude.json after install
        install_lines[1] += f" && rm -f {container_home}/.claude.json"
        lines = [
            "",
            "# Install Claude Code (as paude user)",
            "USER paude",
            f"WORKDIR {container_home}",
            *install_lines,
            "",
            "# Ensure claude is in PATH",
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]
        return lines

    def apply_sandbox_config(self, home: str, workspace: str, args: str) -> str:
        return f"""\
#!/bin/bash
# Auto-generated sandbox config for Claude Code
claude_json="{home}/.claude.json"
settings_json="{home}/.claude/settings.json"
host_ws="${{PAUDE_HOST_WORKSPACE:-}}"

# Suppress trust prompt and onboarding, rewriting host project entry
if [ -f "$claude_json" ]; then
    jq --arg ws "{workspace}" --arg host_ws "$host_ws" '
        (.projects[$host_ws] // {{}}) as $host_data |
        ($host_data * {{hasTrustDialogAccepted: true}}) as $ws_entry |
        .hasCompletedOnboarding = true |
        .projects = {{($ws): $ws_entry}}
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

# Suppress bypass permissions warning when yolo flag is in args
if echo "{args}" | grep -q -- "--dangerously-skip-permissions"; then
    mkdir -p "{home}/.claude" 2>/dev/null || true
    skip_patch='{{"skipDangerousModePermissionPrompt": true}}'
    if [ -f "$settings_json" ]; then
        jq --argjson patch "$skip_patch" '. * $patch' \
            "$settings_json" > "${{settings_json}}.tmp" \\
            && cp -f "${{settings_json}}.tmp" "$settings_json" \\
            && rm -f "${{settings_json}}.tmp"
    else
        echo "$skip_patch" > "$settings_json"
    fi
    chmod g+rw "$settings_json" 2>/dev/null || true
fi
"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"claude {args}"
        return "claude"

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        # Claude seed directory (ro)
        claude_dir = home / ".claude"
        resolved_claude = resolve_path(claude_dir)
        if resolved_claude and resolved_claude.is_dir():
            mounts.extend(["-v", f"{resolved_claude}:/tmp/claude.seed:ro"])

            # Plugins at original host path (ro)
            plugins_dir = resolved_claude / "plugins"
            if plugins_dir.is_dir():
                mounts.extend(["-v", f"{plugins_dir}:{plugins_dir}:ro"])

        # claude.json seed (ro)
        claude_json = home / ".claude.json"
        resolved_claude_json = resolve_path(claude_json)
        if resolved_claude_json and resolved_claude_json.is_file():
            mounts.extend(["-v", f"{resolved_claude_json}:/tmp/claude.json.seed:ro"])

        return mounts

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
