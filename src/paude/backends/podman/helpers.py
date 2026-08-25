"""Podman backend helper functions.

Free functions and naming helpers extracted from PodmanBackend.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from paude.backends.base import Session
from paude.backends.labels import (
    PAUDE_LABEL_APP,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_SESSION,
    SessionSpec,
    read_labels,
    spec_from_labels,
)
from paude.backends.naming import (
    network_name,
    proxy_resource_name,
    resource_name,
    volume_name,
)
from paude.backends.podman.exceptions import SessionNotFoundError
from paude.container.runner import ContainerRunner

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentComposition


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


# Re-export shared naming helpers with Podman-specific aliases
container_name = resource_name
proxy_container_name = proxy_resource_name

# Explicit re-exports for mypy
__all__ = [
    "container_name",
    "proxy_container_name",
    "volume_name",
    "network_name",
]


def ca_volume_name(session_name: str) -> str:
    """Get the CA certificate volume name for a session."""
    return f"paude-ca-{session_name}"


def auth_volume_name(session_name: str) -> str:
    """Get the proxy-only OAuth state volume for a session."""
    return f"paude-auth-{session_name}"


def proxy_secret_prefix(session_name: str) -> str:
    """Get the podman secret name prefix for a session's proxy credentials."""
    return f"paude-proxy-cred-{session_name}-"


def proxy_secret_name(session_name: str, env_var: str) -> str:
    """Get the podman secret name for a proxy credential.

    Args:
        session_name: Session name.
        env_var: Environment variable name (e.g. ``ANTHROPIC_API_KEY``).

    Returns:
        Secret name scoped to the session (e.g.
        ``paude-proxy-cred-my-session-anthropic-api-key``).
    """
    sanitized = env_var.lower().replace("_", "-")
    return f"{proxy_secret_prefix(session_name)}{sanitized}"


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
    backend_type: str = "podman",
) -> Session:
    """Build a Session object from a container dict.

    Args:
        name: Session name.
        container: Raw container dict from list_containers.
        runner: Container runner for proxy health checks.
        backend_type: Backend type string ("podman" or "docker").

    Returns:
        Fully-constructed Session object.
    """
    labels = container.get("Labels", {}) or {}
    view = read_labels(labels)
    status = _check_proxy_health(runner, name, labels, _get_container_status(container))

    return Session(
        name=name,
        status=status,
        # A session whose workspace label was never written must still list;
        # "/" is the placeholder paude has always shown for one.
        workspace=view.workspace or Path("/"),
        created_at=view.created_at,
        backend_type=backend_type,
        container_id=container.get("Id", ""),
        volume_name=volume_name(name),
        agent=view.spec.agent,
        provider=view.spec.provider,
        agent_providers=view.spec.agent_providers,
        # The raw label, deliberately: a listing reports what the session
        # declared. Callers that want the legacy derivation for a session
        # predating the label ask for credential_providers_for_spec.
        credential_providers=view.spec.credential_providers,
        version=view.version,
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


def require_session(runner: ContainerRunner, name: str) -> str:
    """Validate session exists and return its container name."""
    cname = container_name(name)
    if not runner.container_exists(cname):
        raise SessionNotFoundError(f"Session '{name}' not found")
    return cname


def require_running_session(runner: ContainerRunner, name: str) -> str:
    """Validate session exists and is running, return its container name."""
    cname = require_session(runner, name)
    if not runner.container_running(cname):
        raise ValueError(
            f"Session '{name}' is not running. Use 'paude start {name}' to start it."
        )
    return cname


def get_session_labels(runner: ContainerRunner, session_name: str) -> dict[str, str]:
    """Look up container labels for a session."""
    container = find_container_by_session_name(runner, session_name)
    return (container.get("Labels", {}) or {}) if container else {}


def get_session_agent(runner: ContainerRunner, session_name: str) -> Agent:
    """Get the agent instance for a session from its container labels."""
    return get_session_composition(runner, session_name).primary


def composition_for_spec(spec: SessionSpec) -> AgentComposition:
    """Build the agent composition a session's declared spec describes.

    The two branches are not interchangeable, which is why deriving a
    composition from ``spec.agent_providers`` alone would be wrong. A session
    created before multi-agent support has no agent-providers label, only a
    primary agent name -- and resolving that through ``get_agent_composition``
    expands the agent's ``bundled_agents``. A legacy ``gascity`` session
    installs claude and gemini alongside itself; take the other branch and it
    silently comes back with one agent instead of three.
    """
    from paude.agents import get_agent, get_agent_composition, get_agents

    if spec.agent_providers:
        return get_agents(
            [name for name, _provider in spec.agent_providers],
            providers={
                name: provider for name, provider in spec.agent_providers if provider
            },
            include_bundled=False,
        )
    return get_agent_composition(get_agent(spec.agent, provider=spec.provider))


def agent_specs_for(composition: AgentComposition) -> list[tuple[str, str]]:
    """Project a composition back into ordered (agent, provider) pairs.

    The inverse of :func:`composition_for_spec`'s first branch, and the one
    place that projection is written -- it feeds container labels, the upgrade
    manifest and the rebuilt SessionConfig, which must not disagree.
    """
    return [
        (item.config.name, item.config.provider or "") for item in composition.agents
    ]


def credential_providers_for_spec(spec: SessionSpec) -> list[str]:
    """Credential providers for a session, derived for legacy sessions.

    The spec carries the raw label; a session created before that label existed
    has none, so fall back to the providers its agents map to.
    """
    if spec.credential_providers:
        return spec.credential_providers
    return list(
        dict.fromkeys(
            agent.config.provider or ""
            for agent in composition_for_spec(spec).agents
            if agent.config.provider
        )
    )


def get_session_composition(
    runner: ContainerRunner, session_name: str
) -> AgentComposition:
    """Get the full agent composition for a session from its labels."""
    return composition_for_spec(
        spec_from_labels(get_session_labels(runner, session_name))
    )


def get_session_credential_providers(
    runner: ContainerRunner, session_name: str
) -> list[str]:
    """Get credential providers, deriving them for legacy sessions."""
    return credential_providers_for_spec(
        spec_from_labels(get_session_labels(runner, session_name))
    )
