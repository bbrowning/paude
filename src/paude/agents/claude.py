"""Claude Code agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import AgentConfig, build_environment_from_config
from paude.mounts import resolve_path

_CLAUDE_CONFIG_EXCLUDES = [
    "/debug",
    "/file-history",
    "/history.jsonl",
    "/paste-cache",
    "/session-env",
    "/shell-snapshots",
    "/stats-cache.json",
    "/tasks",
    "/todos",
    "/projects",
    "/cache",
    "/.git",
]

_CLAUDE_ACTIVITY_FILES = [
    "history.jsonl",
    "debug/*",
]

_CLAUDE_PASSTHROUGH_VARS = [
    "CLAUDE_CODE_USE_VERTEX",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
]

_CLAUDE_PASSTHROUGH_PREFIXES = [
    "CLOUDSDK_AUTH_",
]


class ClaudeAgent:
    """Claude Code agent implementation."""

    def __init__(self) -> None:
        self._config = AgentConfig(
            name="claude",
            display_name="Claude Code",
            process_name="claude",
            session_name="claude",
            install_script="curl -fsSL https://claude.ai/install.sh | bash",
            install_dir=".local/bin",
            env_vars={"DISABLE_AUTOUPDATER": "1"},
            skip_install_env_var="PAUDE_SKIP_AGENT_INSTALL",
            passthrough_env_vars=list(_CLAUDE_PASSTHROUGH_VARS),
            passthrough_env_prefixes=list(_CLAUDE_PASSTHROUGH_PREFIXES),
            config_dir_name=".claude",
            config_file_name=".claude.json",
            config_excludes=list(_CLAUDE_CONFIG_EXCLUDES),
            activity_files=list(_CLAUDE_ACTIVITY_FILES),
            yolo_flag="--dangerously-skip-permissions",
            clear_command="/clear",
            args_env_var="PAUDE_AGENT_ARGS",
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        lines = [
            "",
            "# Install Claude Code (as paude user)",
            "USER paude",
            f"WORKDIR {container_home}",
            f"RUN umask 0002 && {self._config.install_script}",
            "",
            "# Disable auto-updates (version controlled by image rebuild)",
            "ENV DISABLE_AUTOUPDATER=1",
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

# Suppress trust prompt and onboarding
if [ -f "$claude_json" ]; then
    jq --arg ws "{workspace}" '. * {{
        hasCompletedOnboarding: true,
        projects: {{($ws): {{hasTrustDialogAccepted: true}}}}
    }}' "$claude_json" > "${{claude_json}}.tmp" \\
        && mv "${{claude_json}}.tmp" "$claude_json"
else
    jq -n --arg ws "{workspace}" '{{
        hasCompletedOnboarding: true,
        projects: {{($ws): {{hasTrustDialogAccepted: true}}}}
    }}' > "$claude_json"
fi

# Suppress bypass permissions warning when yolo flag is in args
if echo "{args}" | grep -q -- "--dangerously-skip-permissions"; then
    mkdir -p "{home}/.claude" 2>/dev/null || true
    skip_patch='{{"skipDangerousModePermissionPrompt": true}}'
    if [ -f "$settings_json" ]; then
        jq --argjson patch "$skip_patch" '. * $patch' \
            "$settings_json" > "${{settings_json}}.tmp" \\
            && mv "${{settings_json}}.tmp" \
            "$settings_json"
    else
        echo "$skip_patch" > "$settings_json"
    fi
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
