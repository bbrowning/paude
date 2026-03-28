"""Dry-run output for paude."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from paude.agents import get_agent
from paude.config import detect_config, parse_config
from paude.config.dockerfile import generate_workspace_dockerfile
from paude.config.resolver import ResolvedCreateOptions, format_setting
from paude.domains import format_domains_for_display


def show_dry_run(
    flags: dict[str, Any],
    resolved: ResolvedCreateOptions | None = None,
) -> None:
    """Show configuration and what would be done without executing.

    Args:
        flags: Dictionary containing CLI flags.
        resolved: Resolved create options with provenance (optional for
            backward compatibility).
    """
    workspace = Path.cwd()

    typer.echo("Dry-run mode: showing configuration")
    typer.echo("")
    typer.echo(f"Workspace: {workspace}")

    # Detect and parse config
    config_file = detect_config(workspace)
    if config_file:
        typer.echo(f"Configuration: {config_file}")
        try:
            config = parse_config(config_file)
            typer.echo(f"Config type: {config.config_type}")

            # Base image or Dockerfile
            if config.base_image:
                typer.echo(f"Base image: {config.base_image}")
            if config.dockerfile:
                typer.echo(f"Dockerfile: {config.dockerfile}")
            if config.build_context:
                typer.echo(f"Build context: {config.build_context}")

            # Packages
            if config.packages:
                typer.echo(f"Packages: {', '.join(config.packages)}")

            # Setup/post-create command
            if config.post_create_command:
                typer.echo(f"Setup command: {config.post_create_command}")

            # Show what would be built
            if config.base_image or config.dockerfile:
                typer.echo("")
                typer.echo("Would build custom workspace image")
                typer.echo("")
                typer.echo("Generated Dockerfile:")
                typer.echo("-" * 40)
                agent_instance = get_agent(flags.get("agent", "claude"))
                dockerfile = generate_workspace_dockerfile(config, agent=agent_instance)
                for line in dockerfile.split("\n"):
                    typer.echo(f"  {line}")
                typer.echo("-" * 40)

        except Exception as e:
            typer.echo(f"Config parse error: {e}")
    else:
        typer.echo("Configuration: none")
        typer.echo("Using default paude container")

    typer.echo("")

    if resolved is not None:
        _show_resolved_flags(flags, resolved)
    else:
        _show_legacy_flags(flags)


def _show_resolved_flags(
    flags: dict[str, Any], resolved: ResolvedCreateOptions
) -> None:
    """Show flags with provenance from resolved options."""
    typer.echo("Flags:")
    typer.echo(format_setting("backend", resolved.backend))
    typer.echo(f"  verbose: {flags.get('verbose', False)}")
    typer.echo(format_setting("agent", resolved.agent))
    typer.echo(format_setting("provider", resolved.provider))
    typer.echo(format_setting("yolo", resolved.yolo))
    typer.echo(format_setting("git", resolved.git))

    # Domains with provenance
    allowed_domains = flags.get("allowed_domains")
    domains_display = format_domains_for_display(allowed_domains)
    if resolved.allowed_domains_provenance:
        typer.echo(f"  allowed-domains: {domains_display}")
        for domains, source in resolved.allowed_domains_provenance:
            typer.echo(f"    {', '.join(domains)}  ({source})")
    else:
        typer.echo(f"  allowed-domains: {domains_display}  (built-in)")

    typer.echo(f"  rebuild: {flags.get('rebuild', False)}")

    backend_val = resolved.backend.value
    if backend_val == "openshift":
        typer.echo(format_setting("openshift-context", resolved.openshift_context))
        typer.echo(format_setting("openshift-namespace", resolved.openshift_namespace))
        typer.echo(format_setting("pvc-size", resolved.pvc_size))
        typer.echo(format_setting("credential-timeout", resolved.credential_timeout))

    if resolved.gpu.value:
        typer.echo(format_setting("gpu", resolved.gpu))

    if resolved.platform.value is not None:
        typer.echo(format_setting("platform", resolved.platform))

    if flags.get("claude_args"):
        typer.echo(f"  args: {flags['claude_args']}")


def _show_legacy_flags(flags: dict[str, Any]) -> None:
    """Show flags without provenance (backward compatibility)."""
    typer.echo("Flags:")
    typer.echo(f"  --backend: {flags.get('backend', 'podman')}")
    typer.echo(f"  --verbose: {flags.get('verbose', False)}")
    typer.echo(f"  --yolo: {flags.get('yolo', False)}")

    allowed_domains = flags.get("allowed_domains")
    domains_display = format_domains_for_display(allowed_domains)
    typer.echo(f"  --allowed-domains: {domains_display}")

    typer.echo(f"  --rebuild: {flags.get('rebuild', False)}")

    backend = flags.get("backend", "podman")
    if backend == "openshift":
        ctx = flags.get("openshift_context") or "(current context)"
        ns = flags.get("openshift_namespace") or "(current namespace)"
        typer.echo(f"  --openshift-context: {ctx}")
        typer.echo(f"  --openshift-namespace: {ns}")

    if flags.get("claude_args"):
        typer.echo(f"  claude_args: {flags['claude_args']}")
