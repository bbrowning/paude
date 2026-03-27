"""Session delete command."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.cli.app import BackendType, app
from paude.cli.helpers import _get_backend_instance, find_session_backend
from paude.registry import RegistryEntry


def _cleanup_remote_config_dir(entry: RegistryEntry | None) -> None:
    """Best-effort cleanup of remote config temp dir from registry entry."""
    if not entry or not entry.remote_config_dir or not entry.ssh_host:
        return
    try:
        from paude.cli.remote_git_setup import _build_transport
        from paude.transport.config_sync import cleanup_remote_configs

        transport = _build_transport(entry.ssh_host, entry.ssh_key)
        if transport:
            cleanup_remote_configs(transport, entry.remote_config_dir)
    except Exception:  # noqa: S110 - best-effort cleanup
        pass


@app.command("delete")
def session_delete(
    name: Annotated[
        str,
        typer.Argument(help="Session name to delete"),
    ],
    confirm: Annotated[
        bool,
        typer.Option(
            "--confirm",
            help="Confirm deletion (required).",
        ),
    ] = False,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Delete a session and all its resources permanently."""
    from paude.cli.remote import _cleanup_session_git_remote, _get_session_workspace

    if not confirm:
        typer.echo(
            f"Deleting session '{name}' will permanently remove all data.",
            err=True,
        )
        typer.echo("Use --confirm to proceed.", err=True)
        raise typer.Exit(1)

    from paude.registry import SessionRegistry

    registry = SessionRegistry()

    # Auto-detect backend if not specified
    if backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            backend, backend_obj = result
            workspace = _get_session_workspace(backend_obj, name)
            try:
                reg_entry = registry.get(name)
                _cleanup_remote_config_dir(reg_entry)
                backend_obj.delete_session(name, confirm=True)
                registry.unregister(name)
                typer.echo(f"Session '{name}' deleted.")
                _cleanup_session_git_remote(name, workspace)
                return
            except Exception as e:
                typer.echo(f"Error deleting session: {e}", err=True)
                raise typer.Exit(1) from None
        else:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)

    backend_instance = _get_backend_instance(
        backend, openshift_context, openshift_namespace
    )
    workspace = _get_session_workspace(backend_instance, name)
    try:
        reg_entry = registry.get(name)
        _cleanup_remote_config_dir(reg_entry)
        backend_instance.delete_session(name, confirm=True)
        registry.unregister(name)
        typer.echo(f"Session '{name}' deleted.")
        _cleanup_session_git_remote(name, workspace)
    except SessionNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error deleting session: {e}", err=True)
        raise typer.Exit(1) from None
