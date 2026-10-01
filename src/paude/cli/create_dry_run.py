"""Dry-run display for ``paude create``."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.config.resolver import ResolvedCreateOptions


def run_create_dry_run(
    *,
    prepare: Callable[[], tuple[list[str], list[str], dict[str, str], bool]],
    allowed_endpoints: list[str],
    extra_env: list[str],
    credential_domains: list[str],
    rebuild: bool,
    verbose: bool,
    resolved: ResolvedCreateOptions,
    composition: AgentComposition,
) -> None:
    """Show what ``paude create`` would do, then exit.

    ``prepare`` is the same bound ``_prepare_session_create`` the real create
    uses, so dry-run reports exactly the domains and args a create would.
    """
    from paude.dry_run import show_dry_run
    from paude.endpoints import warn_for_uncovered_allowed_endpoints

    expanded, parsed_args, _env, _unrestricted = prepare()
    warn_for_uncovered_allowed_endpoints(allowed_endpoints, expanded)
    show_dry_run(
        flags={
            "allowed_domains": expanded,
            "allowed_endpoints": allowed_endpoints,
            "rebuild": rebuild,
            "verbose": verbose,
            "claude_args": parsed_args,
            "extra_env": extra_env,
            "credential_domains": credential_domains,
        },
        resolved=resolved,
        composition=composition,
    )
    raise typer.Exit()
