"""Gemini CLI agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import AgentConfig, build_environment_from_config
from paude.mounts import resolve_path

_GEMINI_PASSTHROUGH_VARS = [
    "GOOGLE_CLOUD_PROJECT",
]

_GEMINI_PASSTHROUGH_PREFIXES = [
    "CLOUDSDK_AUTH_",
]


class GeminiAgent:
    """Gemini CLI agent implementation."""

    def __init__(self) -> None:
        self._config = AgentConfig(
            name="gemini",
            display_name="Gemini CLI",
            process_name="gemini",
            session_name="gemini",
            # Runtime fallback only — requires Node.js already in the image.
            # Normal path: dockerfile_install_lines bakes Node.js + CLI into image,
            # and install_agent() skips via `command -v gemini`.
            install_script="npm install -g @google/gemini-cli",
            install_dir=".local/bin",
            env_vars={},
            passthrough_env_vars=list(_GEMINI_PASSTHROUGH_VARS),
            passthrough_env_prefixes=list(_GEMINI_PASSTHROUGH_PREFIXES),
            config_dir_name=".gemini",
            config_file_name=None,
            config_excludes=[],
            activity_files=[],
            yolo_flag="--yolo",
            clear_command=None,
            extra_domain_aliases=["gemini", "nodejs"],
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        lines = [
            "",
            "# Install Node.js for Gemini CLI",
            "USER root",
            "RUN dnf module enable -y nodejs:20"
            " && dnf install -y nodejs npm && dnf clean all",
            "",
            "# Install Gemini CLI",
            "RUN npm install -g @google/gemini-cli",
            "",
            "# Set up home directory",
            "USER paude",
            f"WORKDIR {container_home}",
            "",
            "# Fix permissions for OpenShift arbitrary UID compatibility",
            "USER root",
            f"RUN chmod -R g+rwX {container_home}",
        ]
        return lines

    def apply_sandbox_config(self, home: str, workspace: str, args: str) -> str:
        return f"""\
#!/bin/bash
# Pre-trust the workspace folder so Gemini doesn't prompt on every connect
trusted_json="{home}/.gemini/trustedFolders.json"
mkdir -p "{home}/.gemini" 2>/dev/null || true
if [ -f "$trusted_json" ]; then
    jq --arg ws "{workspace}" '. + {{($ws): "TRUST_FOLDER"}}' \\
        "$trusted_json" > "${{trusted_json}}.tmp" \\
        && mv "${{trusted_json}}.tmp" "$trusted_json"
else
    jq -n --arg ws "{workspace}" '{{($ws): "TRUST_FOLDER"}}' > "$trusted_json"
fi
"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"gemini {args}"
        return "gemini"

    def host_config_mounts(self, home: Path) -> list[str]:
        mounts: list[str] = []

        # Gemini seed directory (ro)
        gemini_dir = home / ".gemini"
        resolved_gemini = resolve_path(gemini_dir)
        if resolved_gemini and resolved_gemini.is_dir():
            mounts.extend(["-v", f"{resolved_gemini}:/tmp/gemini.seed:ro"])

        return mounts

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)
