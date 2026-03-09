"""CLI package for paude.

Submodules register their commands on the shared ``app`` instance via
side-effect imports below.  Only symbols that are imported from
``paude.cli`` elsewhere in the codebase are re-exported here.
"""

from __future__ import annotations

from typing import Annotated

import typer

# Side-effect imports: each submodule registers @app.command() decorators.
import paude.cli.commands as _commands  # noqa: F401
import paude.cli.create as _create  # noqa: F401
import paude.cli.domains as _domains  # noqa: F401
import paude.cli.remote as _remote  # noqa: F401
import paude.cli.status as _status  # noqa: F401
from paude.cli.app import app as app
from paude.cli.app import version_callback
from paude.cli.commands import session_list
from paude.cli.create import session_create as session_create
from paude.cli.help import help_callback
from paude.cli.helpers import _parse_copy_path as _parse_copy_path
from paude.cli.helpers import find_session_backend as find_session_backend


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Show paude version and exit.",
        ),
    ] = False,
    help_opt: Annotated[
        bool,
        typer.Option(
            "--help",
            "-h",
            callback=help_callback,
            is_eager=True,
            help="Show this help message and exit.",
        ),
    ] = False,
) -> None:
    """Run Claude Code in an isolated container."""
    # If a subcommand is invoked, let it handle things
    if ctx.invoked_subcommand is not None:
        return

    # Bare 'paude' command shows session list
    session_list()
