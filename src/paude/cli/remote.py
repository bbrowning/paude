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
      cleanup        Remove remotes whose sessions no longer exist
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

    remote_name = f"paude-{session_name}"

    # Try workspace directory first, then fall back to current directory
    cwd = None
    if workspace is not None and workspace.is_dir() and is_git_repository(workspace):
        cwd = workspace
    elif is_git_repository():
        cwd = None  # use current directory
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
        # Warn about unexpected failures, but don't fail the delete
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

    # Collect all active session names
    active_sessions: set[str] = set()
    for session, _ in collect_all_sessions(openshift_context, openshift_namespace):
        active_sessions.add(session.name)

    removed = 0
    for remote_name, _ in remotes:
        # Remote name is "paude-{session_name}"
        session_name = remote_name.removeprefix("paude-")
        if session_name not in active_sessions:
            if git_remote_remove(remote_name):
                typer.echo(f"Removed orphaned remote '{remote_name}'.")
                removed += 1

    if removed == 0:
        typer.echo("No orphaned remotes found.")
    else:
        typer.echo(f"Removed {removed} orphaned remote(s).")


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
    no_clone_origin: bool = False,
) -> bool:
    """Set up git remote, push code and tags, and configure origin after create.

    When an origin URL exists and no_clone_origin is False, attempts to clone
    from origin inside the container (fast datacenter bandwidth), then pushes
    only local-only commits as a delta. Falls back to full push if clone fails.

    Args:
        session_name: Name of the created session.
        backend_type: "podman" or "openshift".
        openshift_context: OpenShift context (if applicable).
        openshift_namespace: OpenShift namespace (if applicable).
        no_clone_origin: Skip clone-from-origin optimization.

    Returns:
        True if all steps succeeded, False if any step failed.
    """
    from paude.git_remote import (
        get_branch_remote_url,
        get_current_branch,
        is_git_repository,
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

    branch = get_current_branch()
    if branch == "HEAD":
        # Detached HEAD — skip clone optimization
        branch = None

    # Resolve origin URL
    origin_url = get_branch_remote_url(branch)
    origin_https_url = ssh_url_to_https(origin_url) if origin_url else None

    # Try clone-from-origin if conditions are met
    cloned = False
    if origin_https_url and branch and not no_clone_origin:
        cloned = _try_clone_from_origin(
            session_name=session_name,
            backend_type=backend_type,
            origin_https_url=origin_https_url,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
        )

    if cloned:
        _setup_after_clone(
            session_name=session_name,
            backend_type=backend_type,
            branch=branch or "main",
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
        )
    else:
        _setup_full_push(
            session_name=session_name,
            backend_type=backend_type,
            branch=branch or "main",
            origin_https_url=origin_https_url,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
        )

    # Set up pre-commit hooks if config exists
    _setup_precommit(
        session_name=session_name,
        backend_type=backend_type,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
    )

    typer.echo("Git setup complete.")
    return True


def _try_clone_from_origin(
    session_name: str,
    backend_type: str,
    origin_https_url: str,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> bool:
    """Try to clone from origin inside the container. Returns True on success."""
    from paude.git_remote import (
        clone_from_origin_openshift,
        clone_from_origin_podman,
    )

    typer.echo(f"Cloning from origin in container ({origin_https_url})...")

    if backend_type == "podman":
        container_name = f"paude-{session_name}"
        success = clone_from_origin_podman(container_name, origin_https_url)
    else:
        pod_name = f"paude-{session_name}-0"
        namespace = openshift_namespace or "default"
        success = clone_from_origin_openshift(
            pod_name, namespace, origin_https_url, context=openshift_context
        )

    if not success:
        typer.echo(
            "Clone from origin failed (private repo or network issue). "
            "Falling back to full push.",
        )
    return success


def _setup_after_clone(
    session_name: str,
    backend_type: str,
    branch: str,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> None:
    """Post-clone setup: add ext:: remote, push delta, set base ref."""
    from paude.git_remote import (
        count_local_only_commits,
        git_push_to_remote,
        set_base_ref_in_container_openshift,
        set_base_ref_in_container_podman,
    )

    # Add ext:: remote (git init on existing repo is a no-op inside _remote_add)
    _remote_add(
        name=session_name,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        push=False,
    )

    # Check if local has commits not in origin. If local is at or behind
    # origin, skip the push — the container already has the right code.
    remote_name = f"paude-{session_name}"
    local_count = count_local_only_commits(branch)

    if local_count is None or local_count > 0:
        # Either we can't tell (no tracking ref) or there are local commits.
        # Push quietly — if it fails, the container still has origin's code.
        if local_count is not None:
            plural = "commit" if local_count == 1 else "commits"
            n_desc = f"{local_count} local {plural}"
        else:
            n_desc = "local commits"
        typer.echo(f"Pushing {n_desc} to container...")
        if not git_push_to_remote(remote_name, branch, quiet=True):
            if local_count is not None:
                typer.echo(
                    "  Note: Could not push local commits (branch has diverged "
                    "from origin). Container has latest origin code.",
                )

    # Set base ref
    if backend_type == "podman":
        set_base_ref_in_container_podman(f"paude-{session_name}")
    else:
        pod_name = f"paude-{session_name}-0"
        namespace = openshift_namespace or "default"
        set_base_ref_in_container_openshift(
            pod_name, namespace, context=openshift_context
        )

    # Tags are already present from clone — skip pushing
    # (local tags would conflict with cloned tags from origin)

    # Origin is already set by clone — skip set_origin_in_container


def _setup_full_push(
    session_name: str,
    backend_type: str,
    branch: str,
    origin_https_url: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> None:
    """Original full-push flow: init, push all, set origin."""
    from paude.git_remote import (
        git_push_tags_to_remote,
        git_push_to_remote,
        set_base_ref_in_container_openshift,
        set_base_ref_in_container_podman,
        set_origin_in_container_openshift,
        set_origin_in_container_podman,
    )

    # Add remote and init git in container (without pushing)
    _remote_add(
        name=session_name,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        push=False,
    )

    # Push current branch
    remote_name = f"paude-{session_name}"
    typer.echo(f"Pushing {branch} to container...")
    if not git_push_to_remote(remote_name, branch):
        typer.echo("Warning: Failed to push branch.", err=True)
        return

    # Set base ref
    if backend_type == "podman":
        container_name = f"paude-{session_name}"
        set_base_ref_in_container_podman(container_name)
    else:
        pod_name = f"paude-{session_name}-0"
        namespace = openshift_namespace or "default"
        set_base_ref_in_container_openshift(
            pod_name, namespace, context=openshift_context
        )

    # Push tags
    typer.echo("Pushing tags...")
    if not git_push_tags_to_remote(remote_name):
        typer.echo("Warning: Failed to push tags.", err=True)

    # Set origin in container if available
    if origin_https_url:
        typer.echo(f"Setting origin in container to {origin_https_url}...")
        if backend_type == "podman":
            origin_set = set_origin_in_container_podman(
                f"paude-{session_name}", origin_https_url
            )
        else:
            origin_set = set_origin_in_container_openshift(
                f"paude-{session_name}-0",
                openshift_namespace or "default",
                origin_https_url,
                context=openshift_context,
            )
        if not origin_set:
            typer.echo("Warning: Failed to set origin in container.", err=True)
    else:
        typer.echo("No local origin remote found. Skipping origin setup in container.")


def _setup_precommit(
    session_name: str,
    backend_type: str,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> None:
    """Set up pre-commit hooks if config exists."""
    from paude.git_remote import (
        setup_precommit_in_container_openshift,
        setup_precommit_in_container_podman,
    )

    if not Path(".pre-commit-config.yaml").exists():
        return

    typer.echo("Setting up pre-commit hooks in container...")
    if backend_type == "podman":
        success = setup_precommit_in_container_podman(f"paude-{session_name}")
    else:
        success = setup_precommit_in_container_openshift(
            f"paude-{session_name}-0",
            openshift_namespace or "default",
            context=openshift_context,
        )
    if not success:
        typer.echo(
            "Warning: Failed to install pre-commit hooks in container.",
            err=True,
        )


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
        set_base_ref_in_container_openshift,
        set_base_ref_in_container_podman,
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
            # Set base ref to mark initial push point
            if session.backend_type == "openshift":
                set_base_ref_in_container_openshift(
                    pod_name, namespace, context=openshift_context
                )
            else:
                set_base_ref_in_container_podman(container_name)
            typer.echo("Push complete.")
        else:
            typer.echo("")
            typer.echo("Usage:")
            typer.echo(f"  git push {remote_name} {branch}  # Push code to container")
            typer.echo(f"  git pull {remote_name} {branch}  # Pull changes")
            typer.echo(f"  git fetch {remote_name}          # Fetch without merging")
    else:
        raise typer.Exit(1)
