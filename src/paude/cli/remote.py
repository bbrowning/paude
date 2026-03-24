"""Git remote management: remote command and related helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from paude.backends.shared import (
    engine_binary_for_backend,
    is_local_backend,
    pod_name,
    resource_name,
)
from paude.cli.app import app
from paude.cli.helpers import find_session_backend

if TYPE_CHECKING:
    from paude.backends.base import Backend, Session
    from paude.transport.base import Transport


@app.command("remote")
def remote_command(
    action: Annotated[
        str,
        typer.Argument(help="Action: add, list, or remove"),
    ],
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (optional if only one exists)"),
    ] = None,
    push: Annotated[
        bool,
        typer.Option(
            "--push",
            help="Push current branch after adding remote (for 'add' action).",
        ),
    ] = False,
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
    """Manage git remotes for paude sessions.

    Actions:
      add [NAME]     Add a git remote for a session (uses ext:: protocol)
      list           List all paude git remotes
      remove [NAME]  Remove a git remote for a session
      cleanup        Remove remotes whose sessions no longer exist
    """
    from paude.git_remote import (
        git_remote_remove,
        is_git_repository,
        list_paude_remotes,
    )
    from paude.session_discovery import find_workspace_session

    if action == "list":
        remotes = list_paude_remotes()
        if not remotes:
            typer.echo("No paude git remotes found.")
            typer.echo("")
            typer.echo("To add a remote for a session:")
            typer.echo("  paude remote add [SESSION]")
            return

        typer.echo(f"{'REMOTE':<25} {'URL':<60}")
        typer.echo("-" * 85)
        for remote_name, remote_url in remotes:
            url_display = remote_url
            if len(url_display) > 60:
                url_display = url_display[:57] + "..."
            typer.echo(f"{remote_name:<25} {url_display:<60}")
        return

    if action == "add":
        if not is_git_repository():
            typer.echo("Error: Not a git repository.", err=True)
            typer.echo("Initialize git first: git init", err=True)
            raise typer.Exit(1)

        _remote_add(name, openshift_context, openshift_namespace, push=push)
        return

    if action == "remove":
        if not is_git_repository():
            typer.echo("Error: Not a git repository.", err=True)
            raise typer.Exit(1)

        if not name:
            sess_result = find_workspace_session(openshift_context, openshift_namespace)
            if sess_result:
                name = sess_result[0].name
            else:
                typer.echo("Error: Specify a session name to remove.", err=True)
                raise typer.Exit(1)

        rname = resource_name(name)
        if git_remote_remove(rname):
            typer.echo(f"Removed git remote '{rname}'.")
        else:
            raise typer.Exit(1)
        return

    if action == "cleanup":
        if not is_git_repository():
            typer.echo("Error: Not a git repository.", err=True)
            raise typer.Exit(1)

        _remote_cleanup(openshift_context, openshift_namespace)
        return

    typer.echo(f"Unknown action: {action}", err=True)
    typer.echo("Valid actions: add, list, remove, cleanup", err=True)
    raise typer.Exit(1)


def _get_session_workspace(backend: Backend, name: str) -> Path | None:
    """Get the workspace path for a session, or None if unavailable."""
    try:
        session = backend.get_session(name)
        if session is not None:
            return session.workspace
    except Exception:  # noqa: S110
        pass
    return None


def _cleanup_session_git_remote(
    session_name: str, workspace: Path | None = None
) -> None:
    """Remove git remote for a session from the workspace directory.

    Uses the stored workspace path to find and remove the remote, falling back
    to the current directory if workspace is unavailable.

    This is called after session deletion to clean up any associated git remote.
    Failures are silently ignored to not disrupt the deletion workflow.
    """
    from paude.git_remote import is_git_repository

    remote_name = resource_name(session_name)

    cwd = None
    if workspace is not None and workspace.is_dir() and is_git_repository(workspace):
        cwd = workspace
    elif is_git_repository():
        cwd = None
    else:
        return

    result = subprocess.run(
        ["git", "remote", "remove", remote_name],
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode == 0:
        typer.echo(f"Removed git remote '{remote_name}'.")
    elif "No such remote" not in result.stderr:
        err_msg = result.stderr.strip()
        typer.echo(f"Warning: Failed to remove git remote: {err_msg}", err=True)


def _remote_cleanup(
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> None:
    """Remove paude git remotes whose sessions no longer exist."""
    from paude.git_remote import git_remote_remove, list_paude_remotes
    from paude.session_discovery import collect_all_sessions

    remotes = list_paude_remotes()
    if not remotes:
        typer.echo("No paude git remotes found.")
        return

    active_sessions: set[str] = set()
    all_sessions, _ = collect_all_sessions(openshift_context, openshift_namespace)
    for session, _ in all_sessions:
        active_sessions.add(session.name)

    removed = 0
    for remote_name, _ in remotes:
        session_name = remote_name.removeprefix("paude-")
        if session_name not in active_sessions:
            if git_remote_remove(remote_name):
                typer.echo(f"Removed orphaned remote '{remote_name}'.")
                removed += 1

    if removed == 0:
        typer.echo("No orphaned remotes found.")
    else:
        typer.echo(f"Removed {removed} orphaned remote(s).")


def _remote_add(
    name: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
    push: bool = False,
    transport: Transport | None = None,
) -> None:
    """Add a git remote for a session."""
    from paude.git_remote import (
        enable_ext_protocol,
        get_current_branch,
        git_remote_add,
        is_ext_protocol_allowed,
    )

    if not is_ext_protocol_allowed():
        typer.echo("Enabling git ext:: protocol for this repository...", err=True)
        if not enable_ext_protocol():
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            typer.echo(
                "Run manually: git config protocol.ext.allow always",
                err=True,
            )
            raise typer.Exit(1)

    # Resolve session
    session = None
    if name:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            _, backend_obj = result
            session = backend_obj.get_session(name)
    else:
        from paude.session_discovery import find_workspace_session

        ws_result = find_workspace_session(openshift_context, openshift_namespace)
        if ws_result:
            session = ws_result[0]

    if not session:
        typer.echo("Error: No session found.", err=True)
        if name:
            typer.echo(f"Session '{name}' does not exist.", err=True)
        else:
            typer.echo("No session exists for current workspace.", err=True)
            typer.echo("Create one first: paude create", err=True)
        raise typer.Exit(1)

    rname = resource_name(session.name)
    branch = get_current_branch() or "main"

    if not is_local_backend(session.backend_type):
        remote_url = _remote_add_openshift(
            session, rname, branch, openshift_context, openshift_namespace
        )
    else:
        remote_url, transport = _remote_add_local(session, rname, branch, transport)

    # Add the remote
    if git_remote_add(rname, remote_url):
        typer.echo(f"Added git remote '{rname}'.")
        if push:
            from paude.cli.remote_git_setup import _push_after_add

            _push_after_add(
                session,
                rname,
                branch,
                openshift_context,
                openshift_namespace,
                transport,
            )
        else:
            typer.echo("")
            typer.echo("Usage:")
            typer.echo(f"  git push {rname} {branch}  # Push code to container")
            typer.echo(f"  git pull {rname} {branch}  # Pull changes")
            typer.echo(f"  git fetch {rname}          # Fetch without merging")
    else:
        raise typer.Exit(1)


def _remote_add_openshift(
    session: Session,
    rname: str,
    branch: str,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> str:
    """Handle OpenShift-specific remote add: check pod, init workspace, build URL."""
    from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig
    from paude.git_remote import (
        build_openshift_remote_url,
        initialize_container_workspace,
        is_pod_running_openshift,
        openshift_exec_builder,
    )

    os_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
    )

    if os_config.namespace:
        namespace = os_config.namespace
    else:
        try:
            os_backend = OpenShiftBackend(config=os_config)
            namespace = os_backend.namespace
        except Exception:
            namespace = "default"

    pname = pod_name(session.name)

    if not is_pod_running_openshift(
        pod_name=pname, namespace=namespace, context=openshift_context
    ):
        typer.echo("Error: Container not running.", err=True)
        typer.echo("Start it first:", err=True)
        typer.echo(f"  paude start {session.name}", err=True)
        raise typer.Exit(1)

    typer.echo("Initializing git repository in container...")
    exec_builder = openshift_exec_builder(pname, namespace, openshift_context)
    if not initialize_container_workspace(exec_builder, branch=branch):
        raise typer.Exit(1)

    return build_openshift_remote_url(
        pod_name=pname, namespace=namespace, context=openshift_context
    )


def _remote_add_local(
    session: Session,
    rname: str,
    branch: str,
    transport: Transport | None,
) -> tuple[str, Transport | None]:
    """Handle local backend remote add: check container, init workspace, build URL."""
    from paude.cli.remote_git_setup import _build_transport
    from paude.git_remote import (
        build_podman_remote_url,
        build_ssh_remote_url,
        initialize_container_workspace,
        is_container_running_podman,
        podman_exec_builder,
    )
    from paude.registry import SessionRegistry

    cname = resource_name(session.name)
    engine = engine_binary_for_backend(session.backend_type)

    registry_entry = SessionRegistry().get(session.name)
    effective_transport = transport
    if effective_transport is None and registry_entry and registry_entry.ssh_host:
        effective_transport = _build_transport(
            registry_entry.ssh_host, registry_entry.ssh_key
        )

    if not is_container_running_podman(
        cname, engine=engine, transport=effective_transport
    ):
        typer.echo("Error: Container not running.", err=True)
        typer.echo("Start it first:", err=True)
        typer.echo(f"  paude start {session.name}", err=True)
        raise typer.Exit(1)

    typer.echo("Initializing git repository in container...")
    exec_builder = podman_exec_builder(cname, engine)
    if not initialize_container_workspace(
        exec_builder, branch=branch, transport=effective_transport
    ):
        raise typer.Exit(1)

    if registry_entry and registry_entry.ssh_host:
        from paude.transport.ssh import parse_ssh_host

        ssh_host_parsed, ssh_port = parse_ssh_host(registry_entry.ssh_host)
        remote_url = build_ssh_remote_url(
            container_name=cname,
            ssh_host=ssh_host_parsed,
            engine=engine,
            ssh_key=registry_entry.ssh_key,
            ssh_port=ssh_port,
        )
    else:
        remote_url = build_podman_remote_url(container_name=cname, engine=engine)

    return remote_url, effective_transport
