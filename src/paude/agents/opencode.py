"""OpenCode agent implementation."""

from __future__ import annotations

from pathlib import Path

from paude.agents.base import (
    AgentConfig,
    build_environment_from_config,
    build_provider_credentials,
    pipefail_install_lines,
)


class OpenCodeAgent:
    """OpenCode agent implementation."""

    def __init__(self, provider: str | None = None) -> None:
        creds = build_provider_credentials("opencode", provider)
        self._config = AgentConfig(
            name="opencode",
            display_name="OpenCode",
            process_name="opencode",
            session_name="opencode",
            install_script="curl -fsSL https://opencode.ai/install | bash",
            install_dir=".opencode/bin",
            env_vars={"OPENCODE_DISABLE_AUTOUPDATE": "true", **creds.extra_env_vars},
            passthrough_env_vars=creds.passthrough_env_vars,
            secret_env_vars=creds.secret_env_vars,
            passthrough_env_prefixes=creds.passthrough_env_prefixes,
            config_dir_name=".config/opencode",
            config_file_name=None,
            activity_files=[],
            yolo_flag="--auto",
            clear_command=None,
            extra_domain_aliases=["opencode"] + creds.chatgpt_domain_aliases,
            required_domain_aliases=creds.chatgpt_domain_aliases,
            provider=creds.resolved_provider_name,
        )

    @property
    def config(self) -> AgentConfig:
        return self._config

    def dockerfile_install_lines(self, container_home: str) -> list[str]:
        return [
            "",
            "# Install OpenCode",
            "USER paude",
            f"WORKDIR {container_home}",
            *pipefail_install_lines(self._config, container_home),
            "",
            f'ENV PATH="{container_home}/{self._config.install_dir}:$PATH"',
        ]

    def apply_sandbox_config(
        self, home: str, workspace: str, args: str, *, yolo: bool = False
    ) -> str:
        provider_json = _provider_config_json(self._config.provider or "")
        permission_line = '  "permission": {"*": "allow"},' if yolo else ""

        return f"""\
#!/bin/bash
mkdir -p "{home}/.config/opencode" "{home}/.local/share/opencode" 2>/dev/null || true

config_file="{workspace}/opencode.json"
if [ ! -f "$config_file" ]; then
    cat > "$config_file" <<'OCEOF'
{{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "share": "disabled",
{permission_line}
{provider_json}
}}
OCEOF
    chmod g+rw "$config_file" 2>/dev/null || true
fi
"""

    def launch_command(self, args: str) -> str:
        if args:
            return f"opencode {args}"
        return "opencode"

    def host_config_mounts(self, home: Path) -> list[str]:
        return []

    def build_environment(self) -> dict[str, str]:
        return build_environment_from_config(self._config)


_PROVIDER_CONFIGS: dict[str, tuple[str, str | None]] = {
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "OPENAI_API_KEY"),
    "vertex": ("google-vertex", None),
}


def _provider_config_json(provider: str) -> str:
    """Return the provider block for opencode.json."""
    entry = _PROVIDER_CONFIGS.get(provider)
    if entry is None:
        return ""
    name, api_key_var = entry
    if api_key_var:
        options = f'\n        "apiKey": "{{env:{api_key_var}}}"\n      '
    else:
        options = ""
    return f"""\
  "provider": {{
    "{name}": {{
      "options": {{{options}}}
    }}
  }}"""
