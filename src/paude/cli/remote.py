"""Git remote management: remote command and related helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

import typer

from paude.backends.base import Backend, Session
from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig
from paude.cli.app import app
from paude.cli.helpers import find_session_backend
from paude.session_discovery import find_workspace_session


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
    """
    from paude.git_remote import (
        git_remote_remove,
        is_git_repository,
        list_paude_remotes,
    )

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
            # Truncate URL if too long
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
            # Auto-detect session for workspace
            sess, _ = _find_session_for_remote(openshift_context, openshift_namespace)
            if sess:
                name = sess.name
            else:
                typer.echo("Error: Specify a session name to remove.", err=True)
                raise typer.Exit(1)

        remote_name = f"paude-{name}"
        if git_remote_remove(remote_name):
            typer.echo(f"Removed git remote '{remote_name}'.")
        else:
            raise typer.Exit(1)
        return

    typer.echo(f"Unknown action: {action}", err=True)
    typer.echo("Valid actions: add, list, remove", err=True)
    raise typer.Exit(1)


def _cleanup_session_git_remote(session_name: str) -> None:
    """Remove git remote for a session if it exists in current directory.

    This is called after session deletion to clean up any associated git remote.
    Failures are silently ignored to not disrupt the deletion workflow.
    """
    from paude.git_remote import is_git_repository

    if not is_git_repository():
        return

    remote_name = f"paude-{session_name}"

    # Run git remote remove directly to handle "No such remote" silently
    result = subprocess.run(
        ["git", "remote", "remove", remote_name],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        typer.echo(f"Removed git remote '{remote_name}'.")
    elif "No such remote" not in result.stderr:
        # Warn about unexpected failures, but don't fail the delete
        err_msg = result.stderr.strip()
        typer.echo(f"Warning: Failed to remove git remote: {err_msg}", err=True)


def _find_session_for_remote(
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> tuple[Session | None, Backend | None]:
    """Find a session for the current workspace.

    Returns:
        Tuple of (session, backend) if found, (None, None) otherwise.
    """
    result = find_workspace_session(openshift_context, openshift_namespace)
    if result is not None:
        return result
    return (None, None)


def _setup_git_after_create(
    session_name: str,
    backend_type: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> bool:
    """Set up git remote, push code and tags, and configure origin after create.

    Args:
        session_name: Name of the created session.
        backend_type: "podman" or "openshift".
        openshift_context: OpenShift context (if applicable).
        openshift_namespace: OpenShift namespace (if applicable).

    Returns:
        True if all steps succeeded, False if any step failed.
    """
    from paude.git_remote import (
        fetch_tags_in_container_openshift,
        fetch_tags_in_container_podman,
        get_current_branch,
        get_local_origin_url,
        git_push_tags_to_remote,
        git_push_to_remote,
        is_git_repository,
        set_origin_in_container_openshift,
        set_origin_in_container_podman,
        setup_precommit_in_container_openshift,
        setup_precommit_in_container_podman,
        ssh_url_to_https,
    )

    if not is_git_repository():
        typer.echo(
            "Warning: Not in a git repository. Skipping --git setup.",
            err=True,
        )
        return False

    typer.echo("")
    typer.echo("Setting up git...")

    # Step 1: Add remote and init git in container (without pushing)
    _remote_add(
        name=session_name,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        push=False,
    )

    # Step 2: Push current branch
    remote_name = f"paude-{session_name}"
    branch = get_current_branch() or "main"
    typer.echo(f"Pushing {branch} to container...")
    if not git_push_to_remote(remote_name, branch):
        typer.echo("Warning: Failed to push branch.", err=True)
        return False

    # Step 3: Push tags
    typer.echo("Pushing tags...")
    if not git_push_tags_to_remote(remote_name):
        typer.echo("Warning: Failed to push tags.", err=True)
        # Non-fatal, continue

    # Pre-compute container identifiers for Steps 4-6
    if backend_type == "podman":
        container_name = f"paude-{session_name}"
    else:
        pod_name = f"paude-{session_name}-0"
        namespace = openshift_namespace or "default"

    # Step 4: Set origin in container if local origin exists
    origin_url = get_local_origin_url()
    if origin_url:
        # Convert SSH URLs to HTTPS since the container has no SSH keys
        origin_url = ssh_url_to_https(origin_url)
        typer.echo(f"Setting origin in container to {origin_url}...")
        if backend_type == "podman":
            origin_set = set_origin_in_container_podman(container_name, origin_url)
        else:
            origin_set = set_origin_in_container_openshift(
                pod_name,
                namespace,
                origin_url,
                context=openshift_context,
            )

        # Step 5: Fetch tags from origin in container
        if origin_set:
            typer.echo("Fetching tags from origin in container...")
            if backend_type == "podman":
                if not fetch_tags_in_container_podman(container_name):
                    typer.echo(
                        "Warning: Could not fetch tags from origin "
                        "(network may be restricted).",
                        err=True,
                    )
            else:
                if not fetch_tags_in_container_openshift(
                    pod_name,
                    namespace,
                    context=openshift_context,
                ):
                    typer.echo(
                        "Warning: Could not fetch tags from origin "
                        "(network may be restricted).",
                        err=True,
                    )
    else:
        typer.echo("No local origin remote found. Skipping origin setup in container.")

    # Step 6: Set up pre-commit hooks if config exists
    if Path(".pre-commit-config.yaml").exists():
        typer.echo("Setting up pre-commit hooks in container...")
        if backend_type == "podman":
            success = setup_precommit_in_container_podman(container_name)
        else:
            success = setup_precommit_in_container_openshift(
                pod_name,
                namespace,
                context=openshift_context,
            )
        if not success:
            typer.echo(
                "Warning: Failed to install pre-commit hooks in container.",
                err=True,
            )

    typer.echo("Git setup complete.")
    return True


def _remote_add(
    name: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
    push: bool = False,
) -> None:
    """Add a git remote for a session."""
    from paude.git_remote import (
        build_openshift_remote_url,
        build_podman_remote_url,
        enable_ext_protocol,
        get_current_branch,
        git_push_to_remote,
        git_remote_add,
        initialize_container_workspace_openshift,
        initialize_container_workspace_podman,
        is_container_running_podman,
        is_ext_protocol_allowed,
        is_pod_running_openshift,
    )

    # Check if ext protocol is enabled (required for ext:: remotes)
    if not is_ext_protocol_allowed():
        typer.echo("Enabling git ext:: protocol for this repository...", err=True)
        if not enable_ext_protocol():
            typer.echo("Error: Failed to enable git ext:: protocol.", err=True)
            typer.echo(
                "Run manually: git config protocol.ext.allow always",
                err=True,
            )
            raise typer.Exit(1)

    # Find the session
    session = None
    backend_obj = None

    if name:
        # Look up by name
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result:
            _, backend_obj = result
            session = backend_obj.get_session(name)
    else:
        # Auto-detect from workspace
        session, backend_obj = _find_session_for_remote(
            openshift_context, openshift_namespace
        )

    if not session:
        typer.echo("Error: No session found.", err=True)
        if name:
            typer.echo(f"Session '{name}' does not exist.", err=True)
        else:
            typer.echo("No session exists for current workspace.", err=True)
            typer.echo("", err=True)
            typer.echo("Create one first:", err=True)
            typer.echo("  paude create", err=True)
        raise typer.Exit(1)

    # Build the remote URL based on backend type
    remote_name = f"paude-{session.name}"
    branch = get_current_branch() or "main"

    if session.backend_type == "openshift":
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )

        # Get namespace
        if os_config.namespace:
            namespace = os_config.namespace
        else:
            try:
                os_backend = OpenShiftBackend(config=os_config)
                namespace = os_backend.namespace
            except Exception:
                namespace = "default"

        pod_name = f"paude-{session.name}-0"

        # Check if pod is running (live check, not cached status)
        if not is_pod_running_openshift(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
        ):
            typer.echo("Error: Container not running.", err=True)
            typer.echo("Start it first:", err=True)
            typer.echo(f"  paude start {session.name}", err=True)
            raise typer.Exit(1)

        # Initialize git repository in container
        typer.echo("Initializing git repository in container...")
        if not initialize_container_workspace_openshift(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
            branch=branch,
        ):
            raise typer.Exit(1)

        remote_url = build_openshift_remote_url(
            pod_name=pod_name,
            namespace=namespace,
            context=openshift_context,
        )
    else:
        container_name = f"paude-{session.name}"

        # Check if container is running
        if not is_container_running_podman(container_name):
            typer.echo("Error: Container not running.", err=True)
            typer.echo("Start it first:", err=True)
            typer.echo(f"  paude start {session.name}", err=True)
            raise typer.Exit(1)

        # Initialize git repository in container
        typer.echo("Initializing git repository in container...")
        if not initialize_container_workspace_podman(container_name, branch=branch):
            raise typer.Exit(1)

        remote_url = build_podman_remote_url(container_name=container_name)

    # Add the remote
    if git_remote_add(remote_name, remote_url):
        typer.echo(f"Added git remote '{remote_name}'.")

        if push:
            typer.echo("")
            typer.echo(f"Pushing {branch} to container...")
            if not git_push_to_remote(remote_name, branch):
                typer.echo("Push failed.", err=True)
                raise typer.Exit(1)
            typer.echo("Push complete.")
        else:
            typer.echo("")
            typer.echo("Usage:")
            typer.echo(f"  git push {remote_name} {branch}  # Push code to container")
            typer.echo(f"  git pull {remote_name} {branch}  # Pull changes")
            typer.echo(f"  git fetch {remote_name}          # Fetch without merging")
    else:
        raise typer.Exit(1)
