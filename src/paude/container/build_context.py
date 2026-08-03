"""Build context preparation for container image builds."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from paude.config.models import PaudeConfig

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentComposition


def resolve_entrypoint(script_dir: Path | None) -> Path:
    """Resolve the entrypoint.sh path based on script directory."""
    if script_dir:
        return script_dir / "containers" / "paude" / "entrypoint.sh"
    # Bundled via hatchling force-include into paude/container/data/
    return Path(__file__).parent / "data" / "entrypoint.sh"


def copy_entrypoints(entrypoint: Path, dest_dir: Path) -> None:
    """Copy entrypoint scripts to build context with Unix line endings."""
    entrypoint_dest = dest_dir / "entrypoint.sh"
    if entrypoint.exists():
        content = entrypoint.read_text().replace("\r\n", "\n")
        entrypoint_dest.write_text(content, newline="\n")
    else:
        entrypoint_dest.write_text('#!/bin/bash\nexec claude "$@"\n', newline="\n")
    entrypoint_dest.chmod(0o755)

    entrypoint_session = entrypoint.parent / "entrypoint-session.sh"
    entrypoint_session_dest = dest_dir / "entrypoint-session.sh"
    if entrypoint_session.exists():
        content = entrypoint_session.read_text().replace("\r\n", "\n")
        entrypoint_session_dest.write_text(content, newline="\n")
    else:
        entrypoint_session_dest.write_text('#!/bin/bash\nexec "$@"\n', newline="\n")
    entrypoint_session_dest.chmod(0o755)

    tmux_conf = entrypoint.parent / "tmux.conf"
    tmux_conf_dest = dest_dir / "tmux.conf"
    if tmux_conf.exists():
        content = tmux_conf.read_text().replace("\r\n", "\n")
        tmux_conf_dest.write_text(content, newline="\n")
    else:
        tmux_conf_dest.write_text("# auto-generated\n", newline="\n")

    for lib_name in [
        "entrypoint-lib-credentials.sh",
        "entrypoint-lib-config.sh",
        "entrypoint-lib-install.sh",
        "entrypoint-lib-openclaw.sh",
        "patch-proxy-fetch.sh",
        "patch-gemini-otel-proxy.sh",
        "patch-openclaw-otel-proxy.sh",
        "patch-openclaw-otel-logs.sh",
    ]:
        lib_src = entrypoint.parent / lib_name
        lib_dest = dest_dir / lib_name
        if lib_src.exists():
            content = lib_src.read_text().replace("\r\n", "\n")
            lib_dest.write_text(content, newline="\n")
            lib_dest.chmod(0o755)


def generate_dockerfile_content(
    config: PaudeConfig,
    using_default_paude_image: bool,
    include_claude_install: bool = False,
    agent: Agent | None = None,
    composition: AgentComposition | None = None,
) -> str:
    """Generate Dockerfile content for a configured workspace."""
    if using_default_paude_image:
        from paude.config.dockerfile import generate_pip_install_dockerfile

        if composition is None:
            content = generate_pip_install_dockerfile(
                config,
                include_claude_install=include_claude_install,
                agent=agent,
            )
        else:
            content = generate_pip_install_dockerfile(
                config,
                include_claude_install=include_claude_install,
                agent=agent,
                composition=composition,
            )
    else:
        from paude.config.dockerfile import generate_workspace_dockerfile

        if composition is None:
            content = generate_workspace_dockerfile(config, agent=agent)
        else:
            content = generate_workspace_dockerfile(
                config, agent=agent, composition=composition
            )

    return content
