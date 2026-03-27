"""Configuration management commands for paude."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from paude.config.resolver import SettingValue, format_setting
from paude.config.user_config import _user_config_path, load_user_defaults

config_app = typer.Typer(
    name="config",
    help="Manage paude configuration.",
    add_completion=False,
)


@config_app.command("show")
def config_show() -> None:
    """Show resolved defaults for the current directory."""
    from paude.config import detect_config, parse_config
    from paude.config.resolver import resolve_create_options

    user_defaults = load_user_defaults()
    workspace = Path.cwd()

    config_file = detect_config(workspace)
    project_config = None
    if config_file:
        try:
            project_config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Warning: Cannot parse {config_file}: {e}", err=True)

    resolved = resolve_create_options(
        cli_backend=None,
        cli_agent=None,
        cli_yolo=None,
        cli_git=None,
        cli_pvc_size=None,
        cli_credential_timeout=None,
        cli_platform=None,
        cli_openshift_context=None,
        cli_openshift_namespace=None,
        cli_gpu=None,
        cli_allowed_domains=None,
        project_config=project_config,
        user_defaults=user_defaults,
    )

    config_path = _user_config_path()
    typer.echo(f"User config: {config_path}")
    if config_path.exists():
        typer.echo("  (loaded)")
    else:
        typer.echo("  (not found, using built-in defaults)")

    if config_file:
        typer.echo(f"Project config: {config_file}")
    else:
        typer.echo("Project config: none")

    typer.echo("")
    typer.echo("Resolved defaults:")
    settings: list[tuple[str, SettingValue[Any]]] = [
        ("backend", resolved.backend),
        ("agent", resolved.agent),
        ("yolo", resolved.yolo),
        ("git", resolved.git),
        ("pvc-size", resolved.pvc_size),
        ("credential-timeout", resolved.credential_timeout),
        ("platform", resolved.platform),
        ("openshift-context", resolved.openshift_context),
        ("openshift-namespace", resolved.openshift_namespace),
        ("openshift-resources", resolved.openshift_resources),
        ("openshift-build-resources", resolved.openshift_build_resources),
    ]
    for name, setting in settings:
        typer.echo(format_setting(name, setting))

    # Domains
    if resolved.allowed_domains:
        typer.echo("  allowed-domains:")
        for domains, source in resolved.allowed_domains_provenance:
            typer.echo(f"    {', '.join(domains)}  ({source})")
    else:
        typer.echo('  allowed-domains: ["default"]  (built-in)')


@config_app.command("path")
def config_path() -> None:
    """Print the user config file path."""
    typer.echo(str(_user_config_path()))


@config_app.command("init")
def config_init() -> None:
    """Create a starter defaults.json with all fields."""
    path = _user_config_path()
    if path.exists():
        typer.echo(f"Config file already exists: {path}", err=True)
        typer.echo("Edit it directly or delete it to re-initialize.", err=True)
        raise typer.Exit(1)

    starter: dict[str, object] = {
        "defaults": {
            "backend": None,
            "agent": None,
            "yolo": None,
            "git": None,
            "pvc-size": None,
            "credential-timeout": None,
            "platform": None,
            "allowed-domains": [],
            "openshift": {
                "context": None,
                "namespace": None,
                "resources": None,
                "build-resources": None,
            },
        }
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(starter, indent=2) + "\n")
    typer.echo(f"Created {path}")
    typer.echo("Edit this file to set your personal defaults.")
