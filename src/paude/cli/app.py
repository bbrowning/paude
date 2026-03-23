"""Typer app definition, BackendType enum, version/help callbacks, and main."""

from __future__ import annotations

import os
from enum import StrEnum

import typer

from paude import __version__
from paude.cli.help import PaudeGroup

app = typer.Typer(
    name="paude",
    cls=PaudeGroup,
    help="Run AI coding agents in isolated containers.",
    add_completion=False,
    context_settings={
        "allow_interspersed_args": False,
        "help_option_names": ["-h", "--help"],
    },
)


class BackendType(StrEnum):
    """Container backend types."""

    podman = "podman"
    docker = "docker"
    openshift = "openshift"


def version_callback(value: bool) -> None:
    """Print version information and exit."""
    if value:
        typer.echo(f"paude {__version__}")
        dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
        if dev_mode:
            typer.echo("  mode: development (PAUDE_DEV=1, building locally)")
        else:
            typer.echo(f"  mode: installed (pulling from {registry})")
        raise typer.Exit()
