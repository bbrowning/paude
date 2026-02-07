"""Session discovery helpers for finding sessions across backends."""

from __future__ import annotations

from pathlib import Path

import typer

from paude.backends import PodmanBackend, Session
from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

Backend = PodmanBackend | OpenShiftBackend


def create_openshift_backend(
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> OpenShiftBackend | None:
    """Create an OpenShift backend if available.

    Returns None if OpenShift is not reachable or oc is not installed.
    """
    try:
        os_config = OpenShiftConfig(
            context=openshift_context,
            namespace=openshift_namespace,
        )
        return OpenShiftBackend(config=os_config)
    except Exception:  # noqa: S110 - OpenShift may not be available
        return None


def find_workspace_session(
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    status_filter: str | None = None,
) -> tuple[Session, Backend] | None:
    """Find a session matching the current workspace across all backends.

    Checks Podman first, then OpenShift. Returns the first match found.

    Args:
        openshift_context: Optional OpenShift kubeconfig context.
        openshift_namespace: Optional OpenShift namespace.
        status_filter: If set, only match sessions with this status
            (e.g. "running"). If None, matches any status.

    Returns:
        Tuple of (session, backend) if found, None otherwise.
    """
    workspace = Path.cwd()

    # Check Podman first
    try:
        podman = PodmanBackend()
        session = podman.find_session_for_workspace(workspace)
        if session and (status_filter is None or session.status == status_filter):
            return (session, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Check OpenShift
    os_backend = create_openshift_backend(openshift_context, openshift_namespace)
    if os_backend is not None:
        try:
            session = os_backend.find_session_for_workspace(workspace)
            if session and (status_filter is None or session.status == status_filter):
                return (session, os_backend)
        except Exception:  # noqa: S110
            pass

    return None


def collect_all_sessions(
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    status_filter: str | None = None,
    podman_backend: PodmanBackend | None = None,
    os_backend: OpenShiftBackend | None = None,
) -> list[tuple[Session, Backend]]:
    """Collect sessions from all available backends.

    Args:
        openshift_context: Optional OpenShift kubeconfig context.
        openshift_namespace: Optional OpenShift namespace.
        status_filter: If set, only include sessions with this status
            (e.g. "running"). If None, includes all sessions.
        podman_backend: Reuse an existing PodmanBackend instance.
        os_backend: Reuse an existing OpenShiftBackend instance.

    Returns:
        List of (session, backend) tuples.
    """
    all_sessions: list[tuple[Session, Backend]] = []

    # Try Podman
    if podman_backend is None:
        try:
            podman_backend = PodmanBackend()
        except Exception:  # noqa: S110
            pass

    if podman_backend is not None:
        try:
            for s in podman_backend.list_sessions():
                if status_filter is None or s.status == status_filter:
                    all_sessions.append((s, podman_backend))
        except Exception:  # noqa: S110
            pass

    # Try OpenShift
    if os_backend is None:
        os_backend = create_openshift_backend(openshift_context, openshift_namespace)

    if os_backend is not None:
        try:
            for s in os_backend.list_sessions():
                if status_filter is None or s.status == status_filter:
                    all_sessions.append((s, os_backend))
        except Exception:  # noqa: S110
            pass

    return all_sessions


def resolve_session_for_backend(
    backend: Backend,
    status_filter: str | None = None,
) -> str | None:
    """Find a session name for the current workspace on a specific backend.

    Checks for a workspace match first. If not found, falls back to listing
    all sessions and picking if exactly one exists. Prints messages for
    "no sessions" and "multiple sessions" cases.

    Args:
        backend: The backend to search.
        status_filter: If set, only consider sessions with this status.

    Returns:
        Session name if found, None if user needs to specify.
    """
    workspace = Path.cwd()

    session = backend.find_session_for_workspace(workspace)
    if session and (status_filter is None or session.status == status_filter):
        return session.name

    # List sessions matching the filter
    try:
        all_sessions = backend.list_sessions()
    except Exception:
        all_sessions = []

    if status_filter:
        sessions = [s for s in all_sessions if s.status == status_filter]
    else:
        sessions = all_sessions

    if not sessions:
        _print_no_sessions_message(status_filter, backend)
        return None

    if len(sessions) == 1:
        return sessions[0].name

    # Multiple sessions
    _print_multiple_sessions_message(status_filter, sessions)
    return None


def _print_no_sessions_message(
    status_filter: str | None,
    backend: Backend,
) -> None:
    """Print the appropriate 'no sessions found' message."""
    if status_filter == "running":
        typer.echo("No running sessions to stop.", err=True)
    else:
        backend_flag = ""
        if isinstance(backend, OpenShiftBackend):
            backend_flag = " --backend=openshift"
        typer.echo("No sessions found for this workspace.", err=True)
        typer.echo("", err=True)
        typer.echo("To create and start a session:", err=True)
        typer.echo(
            f"  paude create{backend_flag} && paude start{backend_flag}",
            err=True,
        )


def _print_multiple_sessions_message(
    status_filter: str | None,
    sessions: list[Session],
) -> None:
    """Print the appropriate 'multiple sessions found' message."""
    if status_filter == "running":
        typer.echo(
            "Multiple running sessions found. Specify a name:",
            err=True,
        )
        for s in sessions:
            typer.echo(f"  {s.name}")
    else:
        typer.echo("Multiple sessions found. Specify a name:", err=True)
        for s in sessions:
            typer.echo(f"  {s.name} ({s.status})")
