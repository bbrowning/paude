"""Backend naming and type classification helpers."""

from __future__ import annotations


def resource_name(session_name: str) -> str:
    """Get the resource name for a session (container, StatefulSet, git remote)."""
    return f"paude-{session_name}"


def proxy_resource_name(session_name: str) -> str:
    """Get the proxy resource name for a session (deployment, container, service)."""
    return f"paude-proxy-{session_name}"


def volume_name(session_name: str) -> str:
    """Get the volume name for a session (Podman volume)."""
    return f"paude-{session_name}-workspace"


def network_name(session_name: str) -> str:
    """Get the network name for a session (Podman network)."""
    return f"paude-net-{session_name}"


LOCAL_BACKEND_TYPES = frozenset({"podman", "docker"})


def is_local_backend(backend_type: str) -> bool:
    """Check if a backend type is a local container engine (podman or docker)."""
    return backend_type in LOCAL_BACKEND_TYPES


def engine_binary_for_backend(backend_type: str) -> str:
    """Get the container engine binary for a backend type.

    Returns "podman" for "podman", "docker" for "docker".
    Raises ValueError for non-local backend types.
    """
    if backend_type in LOCAL_BACKEND_TYPES:
        return backend_type
    raise ValueError(f"No engine binary for backend type: {backend_type}")
