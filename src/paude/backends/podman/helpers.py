"""Podman backend helper functions.

Free functions and naming helpers extracted from PodmanBackend.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from paude.backends.base import Session
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    decode_path,
)
from paude.container.runner import ContainerRunner


def _get_container_status(container: dict[str, Any]) -> str:
    """Extract session status from container info.

    Handles different Podman versions which may return State as:
    - A string: "running", "exited", "created", etc.
    - A dict: {"Status": "running", ...}

    Also checks "Status" field as fallback.
    """
    state = container.get("State", "")

    # Handle dict format (some Podman versions)
    if isinstance(state, dict):
        state = state.get("Status", "") or state.get("status", "")

    # Fallback to Status field if State is empty/missing
    if not state:
        state = container.get("Status", "unknown")

    # Normalize to lowercase string
    if not isinstance(state, str):
        state = str(state)
    state = state.lower()

    # Map container state to session status
    status_map = {
        "running": "running",
        "exited": "stopped",
        "stopped": "stopped",
        "created": "stopped",
        "paused": "stopped",
        "configured": "stopped",  # Podman 4.x uses this for newly created
        "dead": "error",
        "removing": "error",
    }
    return status_map.get(state, "stopped")  # Default to stopped, not error


def _generate_session_name(workspace: Path) -> str:
    """Generate a session name from workspace path.

    Args:
        workspace: Workspace path.

    Returns:
        Session name (e.g., "my-project-abc123").
    """
    project_name = workspace.name.lower()
    # Sanitize project name for container/volume naming
    project_name = "".join(c if c.isalnum() or c == "-" else "-" for c in project_name)
    project_name = project_name.strip("-")[:20]
    suffix = secrets.token_hex(3)
    return f"{project_name}-{suffix}"


def container_name(session_name: str) -> str:
    """Get container name for a session."""
    return f"paude-{session_name}"


def volume_name(session_name: str) -> str:
    """Get volume name for a session."""
    return f"paude-{session_name}-workspace"


def proxy_container_name(session_name: str) -> str:
    """Get proxy container name for a session."""
    return f"paude-proxy-{session_name}"


def network_name(session_name: str) -> str:
    """Get internal network name for a session."""
    return f"paude-net-{session_name}"


def find_container_by_session_name(
    runner: ContainerRunner, name: str
) -> dict[str, Any] | None:
    """Find a container by session name label.

    Args:
        runner: Container runner instance.
        name: Session name to search for.

    Returns:
        Container dict if found, None otherwise.
    """
    containers = runner.list_containers(label_filter=PAUDE_LABEL_APP)
    for c in containers:
        labels = c.get("Labels", {}) or {}
        if labels.get(PAUDE_LABEL_SESSION) == name:
            return c
    return None


def build_session_from_container(
    name: str,
    container: dict[str, Any],
    runner: ContainerRunner,
) -> Session:
    """Build a Session object from a container dict.

    Args:
        name: Session name.
        container: Raw container dict from list_containers.
        runner: Container runner for proxy health checks.

    Returns:
        Fully-constructed Session object.
    """
    labels = container.get("Labels", {}) or {}

    workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    workspace = (
        decode_path(workspace_encoded, url_safe=True)
        if workspace_encoded
        else Path("/")
    )
    created_at = labels.get(PAUDE_LABEL_CREATED, "")

    status = _get_container_status(container)
    status = _check_proxy_health(runner, name, labels, status)

    agent_name = labels.get(PAUDE_LABEL_AGENT, "claude")

    return Session(
        name=name,
        status=status,
        workspace=workspace,
        created_at=created_at,
        backend_type="podman",
        container_id=container.get("Id", ""),
        volume_name=volume_name(name),
        agent=agent_name,
    )


def _check_proxy_health(
    runner: ContainerRunner,
    session_name: str,
    labels: dict[str, str],
    status: str,
) -> str:
    """Check if a running session's proxy is healthy.

    Returns "degraded" if the session is running but its expected proxy
    is missing or stopped. Returns the original status otherwise.
    """
    if status != "running":
        return status

    # Check if proxy was configured for this session
    if PAUDE_LABEL_DOMAINS not in labels:
        return status  # No proxy expected

    pname = proxy_container_name(session_name)
    if not runner.container_exists(pname):
        return "degraded"
    if not runner.container_running(pname):
        return "degraded"

    return status
