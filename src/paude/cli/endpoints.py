"""Runtime management for destination-scoped proxy endpoint exceptions."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.base import Backend
from paude.cli.app import BackendType, app
from paude.cli.helpers import _get_backend_instance, find_session_backend
from paude.endpoints import normalize_allowed_endpoints


def _resolve_backend(name: str, backend: BackendType | None) -> Backend:
    """Resolve the explicitly selected or session-owning backend."""
    if backend is not None:
        return _get_backend_instance(backend)
    result = find_session_backend(name)
    if result is None:
        typer.echo(f"Session '{name}' not found.", err=True)
        raise typer.Exit(1)
    return result[1]


def _updated_endpoints(
    current: list[str],
    *,
    add: list[str] | None,
    remove: list[str] | None,
    replace: list[str] | None,
) -> tuple[list[str], str]:
    """Apply one requested list operation and return its result and verb."""
    if add:
        additions = normalize_allowed_endpoints(add)
        return list(dict.fromkeys([*current, *additions])), "Added"
    if remove:
        removal = set(normalize_allowed_endpoints(remove))
        return [item for item in current if item not in removal], "Removed"
    if replace:
        return normalize_allowed_endpoints(replace), "Replaced"
    return current, "Listed"


@app.command("allowed-endpoints")
def allowed_endpoints_cmd(
    name: Annotated[str, typer.Argument(help="Session name.")],
    add: Annotated[
        list[str] | None,
        typer.Option("--add", help="Add exact host:port exceptions."),
    ] = None,
    remove: Annotated[
        list[str] | None,
        typer.Option("--remove", help="Remove exact host:port exceptions."),
    ] = None,
    replace: Annotated[
        list[str] | None,
        typer.Option("--replace", help="Replace the entire endpoint list."),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if omitted).",
        ),
    ] = None,
) -> None:
    """Manage exact destination-scoped nonstandard-port exceptions."""
    if sum(option is not None for option in (add, remove, replace)) > 1:
        typer.echo(
            "Error: Only one of --add, --remove, --replace can be specified.",
            err=True,
        )
        raise typer.Exit(1)

    backend_obj = _resolve_backend(name, backend)
    try:
        current = backend_obj.get_allowed_endpoints(name)
        if current is None:
            raise ValueError(
                "Session was created without a proxy. Recreate it to enable "
                "endpoint filtering."
            )
        updated, verb = _updated_endpoints(
            current, add=add, remove=remove, replace=replace
        )
        if verb == "Listed":
            typer.echo(f"Allowed endpoints for session '{name}':")
            output = "  (none)" if not current else "\n".join(f"  {e}" for e in current)
            typer.echo(output)
            return
        backend_obj.update_allowed_endpoints(name, updated)
        typer.echo(
            f"{verb} allowed endpoints for session '{name}' ({len(updated)} total)."
        )
    except (SessionNotFoundError, ValueError, NotImplementedError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error managing endpoints: {e}", err=True)
        raise typer.Exit(1) from None
