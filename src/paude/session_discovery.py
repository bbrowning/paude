"""Session discovery helpers for finding sessions across backends."""

from __future__ import annotations

from pathlib import Path

import typer

from paude.backends import PodmanBackend, Session
from paude.backends.base import Backend
from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig
from paude.container.engine import ContainerEngine


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


def _status_matches(session_status: str, status_filter: str | None) -> bool:
    """Check if a session status matches the filter.

    Treats "degraded" as matching "running" since the main container is
    still running (the proxy is missing/stopped).
    """
    if status_filter is None:
        return True
    if session_status == status_filter:
        return True
    # A degraded session is still running (just missing its proxy)
    if status_filter == "running" and session_status == "degraded":
        return True
    return False


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
        if session and (_status_matches(session.status, status_filter)):
            return (session, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Check Docker
    try:
        docker = PodmanBackend(engine=ContainerEngine("docker"))
        session = docker.find_session_for_workspace(workspace)
        if session and (_status_matches(session.status, status_filter)):
            return (session, docker)
    except Exception:  # noqa: S110 - Docker may not be available
        pass

    # Check OpenShift
    os_backend = create_openshift_backend(openshift_context, openshift_namespace)
    if os_backend is not None:
        try:
            session = os_backend.find_session_for_workspace(workspace)
            if session and (_status_matches(session.status, status_filter)):
                return (session, os_backend)
        except Exception:  # noqa: S110
            pass

    # Check SSH sessions from registry
    result = _find_ssh_workspace_session(workspace, status_filter)
    if result is not None:
        return result

    return None


def _build_ssh_backend(entry: object) -> PodmanBackend | None:
    """Reconstruct a PodmanBackend with SSH transport from a registry entry."""
    from paude.backends.shared import build_ssh_backend

    return build_ssh_backend(entry)


def _find_ssh_workspace_session(
    workspace: Path,
    status_filter: str | None = None,
) -> tuple[Session, Backend] | None:
    """Find SSH sessions for the given workspace via the local registry."""
    from paude.registry import SessionRegistry

    registry = SessionRegistry()
    for entry in registry.list_entries():
        if not entry.ssh_host:
            continue
        if entry.workspace and Path(entry.workspace) != workspace:
            continue
        backend = _build_ssh_backend(entry)
        if backend is None:
            continue
        try:
            session = backend.get_session(entry.name)
            if session and _status_matches(session.status, status_filter):
                return (session, backend)
        except Exception:  # noqa: S110 - remote may be unreachable
            pass
    return None


def _collect_ssh_sessions(
    status_filter: str | None = None,
) -> list[tuple[Session, Backend]]:
    """Collect sessions from SSH remotes registered in the local registry."""
    from paude.registry import SessionRegistry

    results: list[tuple[Session, Backend]] = []
    registry = SessionRegistry()
    for entry in registry.list_entries():
        if not entry.ssh_host:
            continue
        backend = _build_ssh_backend(entry)
        if backend is None:
            continue
        try:
            session = backend.get_session(entry.name)
            if session and _status_matches(session.status, status_filter):
                results.append((session, backend))
        except Exception:  # noqa: S110 - remote may be unreachable
            pass
    return results


def collect_all_sessions(
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    status_filter: str | None = None,
    podman_backend: PodmanBackend | None = None,
    os_backend: OpenShiftBackend | None = None,
    *,
    skip_podman: bool = False,
    skip_openshift: bool = False,
) -> tuple[list[tuple[Session, Backend]], set[str]]:
    """Collect sessions from all available backends.

    Args:
        openshift_context: Optional OpenShift kubeconfig context.
        openshift_namespace: Optional OpenShift namespace.
        status_filter: If set, only include sessions with this status
            (e.g. "running"). If None, includes all sessions.
        podman_backend: Reuse an existing PodmanBackend instance.
        os_backend: Reuse an existing OpenShiftBackend instance.
        skip_podman: If True, skip Podman backend entirely.
        skip_openshift: If True, skip OpenShift backend entirely.

    Returns:
        Tuple of (list of (session, backend) tuples, set of reachable
        backend types).
    """
    all_sessions: list[tuple[Session, Backend]] = []
    reachable_backends: set[str] = set()

    # Try Podman
    if not skip_podman:
        if podman_backend is None:
            try:
                podman_backend = PodmanBackend()
            except Exception:  # noqa: S110
                pass

        if podman_backend is not None:
            try:
                for s in podman_backend.list_sessions():
                    if _status_matches(s.status, status_filter):
                        all_sessions.append((s, podman_backend))
                reachable_backends.add("podman")
            except Exception:  # noqa: S110
                pass

        # Also try Docker
        try:
            docker_backend = PodmanBackend(engine=ContainerEngine("docker"))
            for s in docker_backend.list_sessions():
                if _status_matches(s.status, status_filter):
                    all_sessions.append((s, docker_backend))
            reachable_backends.add("docker")
        except Exception:  # noqa: S110
            pass

    # Try OpenShift
    if not skip_openshift:
        if os_backend is None:
            os_backend = create_openshift_backend(
                openshift_context, openshift_namespace
            )

        if os_backend is not None:
            try:
                for s in os_backend.list_sessions():
                    if _status_matches(s.status, status_filter):
                        all_sessions.append((s, os_backend))
                reachable_backends.add("openshift")
            except Exception:  # noqa: S110
                pass

    # Try SSH sessions from registry
    ssh_sessions = _collect_ssh_sessions(status_filter)
    if ssh_sessions:
        # Deduplicate: skip SSH sessions already found locally
        known_names = {s.name for s, _ in all_sessions}
        for s, b in ssh_sessions:
            if s.name not in known_names:
                all_sessions.append((s, b))
        reachable_backends.add("ssh")

    return all_sessions, reachable_backends


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
    if session and (_status_matches(session.status, status_filter)):
        return session.name

    # List sessions matching the filter
    try:
        all_sessions = backend.list_sessions()
    except Exception:
        all_sessions = []

    if status_filter:
        sessions = [s for s in all_sessions if _status_matches(s.status, status_filter)]
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
        elif isinstance(backend, PodmanBackend) and backend.engine.binary == "docker":
            backend_flag = " --backend=docker"
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
