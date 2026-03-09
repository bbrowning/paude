"""Typer app definition, BackendType enum, version/help callbacks, and main."""

from __future__ import annotations

import os
from enum import StrEnum

import typer

from paude import __version__

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)


class BackendType(StrEnum):
    """Container backend types."""

    podman = "podman"
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
