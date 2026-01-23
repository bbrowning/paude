"""Dry-run output for paude."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from paude.config import detect_config, parse_config
from paude.config.dockerfile import generate_workspace_dockerfile


def show_dry_run(flags: dict[str, Any]) -> None:
    """Show configuration and what would be done without executing.

    Output sections:
    1. Workspace path
    2. Configuration file and type (or "none")
    3. Base image or Dockerfile
    4. Additional packages (if any)
    5. Setup command (if any)
    6. Would build: image:hash (if custom)
    7. Generated Dockerfile (if custom)
    8. Flags summary

    Args:
        flags: Dictionary containing CLI flags (yolo, allow_network, rebuild, etc.)
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
                dockerfile = generate_workspace_dockerfile(config)
                for line in dockerfile.split("\n"):
                    typer.echo(f"  {line}")
                typer.echo("-" * 40)

        except Exception as e:
            typer.echo(f"Config parse error: {e}")
    else:
        typer.echo("Configuration: none")
        typer.echo("Using default paude container")

    typer.echo("")
    typer.echo("Flags:")
    typer.echo(f"  --backend: {flags.get('backend', 'podman')}")
    typer.echo(f"  --verbose: {flags.get('verbose', False)}")
    typer.echo(f"  --yolo: {flags.get('yolo', False)}")
    typer.echo(f"  --allow-network: {flags.get('allow_network', False)}")
    typer.echo(f"  --rebuild: {flags.get('rebuild', False)}")

    # OpenShift-specific options
    backend = flags.get("backend", "podman")
    if backend == "openshift":
        ctx = flags.get("openshift_context") or "(current context)"
        ns = flags.get("openshift_namespace") or "(current namespace)"
        typer.echo(f"  --openshift-context: {ctx}")
        typer.echo(f"  --openshift-namespace: {ns}")

    if flags.get("claude_args"):
        typer.echo(f"  claude_args: {flags['claude_args']}")
