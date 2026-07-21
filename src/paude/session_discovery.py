"""Session discovery helpers for finding sessions across backends."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

import typer

from paude.backends import PodmanBackend, Session
from paude.backends.base import Backend
from paude.container.engine import ContainerEngine
from paude.registry import RegistryEntry
from paude.transport.ssh import SSH_STATUS_TIMEOUT


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
    status_filter: str | None = None,
) -> tuple[Session, Backend] | None:
    """Find a session matching the current workspace across all engines.

    Args:
        status_filter: If set, only match sessions with this status.

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

    # Check SSH sessions from registry
    result = _find_ssh_workspace_session(workspace, status_filter)
    if result is not None:
        return result

    return None


def _build_ssh_backend(
    entry: object,
    connect_timeout: int | None = None,
) -> PodmanBackend | None:
    """Reconstruct a PodmanBackend with SSH transport from a registry entry."""
    from paude.backends.shared import build_ssh_backend

    return build_ssh_backend(entry, connect_timeout=connect_timeout)


def _probe_ssh_entry(
    entry: RegistryEntry,
    status_filter: str | None,
) -> tuple[Session, Backend] | None:
    """Probe a single SSH registry entry for status match."""
    backend = _build_ssh_backend(entry, connect_timeout=SSH_STATUS_TIMEOUT)
    if backend is None:
        return None
    session = backend.get_session(entry.name)
    if session and _status_matches(session.status, status_filter):
        return (session, backend)
    return None


def _find_ssh_workspace_session(
    workspace: Path,
    status_filter: str | None = None,
) -> tuple[Session, Backend] | None:
    """Find SSH sessions for the given workspace via the local registry."""
    from paude.registry import SessionRegistry

    registry = SessionRegistry()
    ssh_entries = [
        e
        for e in registry.list_entries()
        if e.ssh_host and (not e.workspace or Path(e.workspace) == workspace)
    ]
    if not ssh_entries:
        return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_probe_ssh_entry, e, status_filter) for e in ssh_entries]
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result is not None:
                    return result
            except Exception:  # noqa: S110 - remote may be unreachable
                pass
    return None


def _collect_ssh_sessions(
    status_filter: str | None = None,
) -> list[tuple[Session, Backend]]:
    """Collect sessions from SSH remotes registered in the local registry."""
    from paude.registry import SessionRegistry

    registry = SessionRegistry()
    ssh_entries = [e for e in registry.list_entries() if e.ssh_host]
    if not ssh_entries:
        return []

    results: list[tuple[Session, Backend]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_probe_ssh_entry, e, status_filter) for e in ssh_entries]
        for fut in as_completed(futures):
            try:
                result = fut.result()
                if result is not None:
                    results.append(result)
            except Exception:  # noqa: S110 - remote may be unreachable
                pass
    return results


def _collect_podman_sessions(
    podman_backend: PodmanBackend | None,
    status_filter: str | None,
) -> list[tuple[Session, Backend]]:
    """Collect sessions from the Podman backend."""
    if podman_backend is None:
        podman_backend = PodmanBackend()
    return [
        (s, podman_backend)
        for s in podman_backend.list_sessions()
        if _status_matches(s.status, status_filter)
    ]


def _collect_docker_sessions(
    status_filter: str | None,
) -> list[tuple[Session, Backend]]:
    """Collect sessions from the Docker backend."""
    docker_backend = PodmanBackend(engine=ContainerEngine("docker"))
    return [
        (s, docker_backend)
        for s in docker_backend.list_sessions()
        if _status_matches(s.status, status_filter)
    ]


def collect_all_sessions(
    status_filter: str | None = None,
    podman_backend: PodmanBackend | None = None,
    *,
    backend_filter: str | None = None,
) -> tuple[list[tuple[Session, Backend]], set[str]]:
    """Collect sessions from supported engines and registered SSH hosts.

    Args:
        status_filter: If set, only include sessions with this status
            (e.g. "running"). If None, includes all sessions.
        podman_backend: Reuse an existing PodmanBackend instance.
        backend_filter: Limit local discovery to "podman" or "docker".

    Returns:
        Tuple of (list of (session, backend) tuples, set of reachable
        backend types).
    """
    all_sessions: list[tuple[Session, Backend]] = []
    reachable_backends: set[str] = set()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures: dict[str, Future[list[tuple[Session, Backend]]]] = {}

        if backend_filter in (None, "podman"):
            futures["podman"] = pool.submit(
                _collect_podman_sessions, podman_backend, status_filter
            )
        if backend_filter in (None, "docker"):
            futures["docker"] = pool.submit(_collect_docker_sessions, status_filter)

        futures["ssh"] = pool.submit(_collect_ssh_sessions, status_filter)

        ssh_sessions: list[tuple[Session, Backend]] = []
        for key, fut in futures.items():
            try:
                sessions = fut.result()
                if key == "ssh":
                    ssh_sessions = sessions
                else:
                    all_sessions.extend(sessions)
                    reachable_backends.add(key)
            except Exception:  # noqa: S110
                pass

    # Deduplicate: skip SSH sessions already found via other backends
    if ssh_sessions:
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
        if isinstance(backend, PodmanBackend) and backend.engine.binary == "docker":
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
