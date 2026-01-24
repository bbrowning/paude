"""Base protocol for container backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Session:
    """Represents a paude session.

    Attributes:
        name: Session name (user-provided or auto-generated).
        status: Session status ("running", "stopped", "error", "pending").
        workspace: Local workspace path.
        created_at: ISO timestamp of session creation.
        backend_type: Backend type ("podman" or "openshift").
        container_id: Backend-specific container/pod identifier.
        volume_name: Backend-specific volume/PVC name.
    """

    name: str
    status: str
    workspace: Path
    created_at: str
    backend_type: str
    container_id: str | None = None
    volume_name: str | None = None


@dataclass
class SessionConfig:
    """Configuration for creating a new session.

    Attributes:
        name: Session name (None for auto-generate).
        workspace: Local workspace path.
        image: Container image to use.
        env: Environment variables.
        mounts: Volume mount arguments (Podman-style).
        args: Arguments to pass to Claude.
        workdir: Working directory inside container.
        network_restricted: Whether to restrict network.
        yolo: Enable YOLO mode.
        pvc_size: PVC size for OpenShift (e.g., "10Gi").
        storage_class: Storage class for OpenShift.
        network: Podman network name for proxy setup.
    """

    name: str | None
    workspace: Path
    image: str
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    workdir: str | None = None
    network_restricted: bool = True
    yolo: bool = False
    pvc_size: str = "10Gi"
    storage_class: str | None = None
    network: str | None = None


class Backend(Protocol):
    """Container backend interface.

    All container backends (Podman, OpenShift) must implement this protocol.
    The CLI delegates to the appropriate backend based on configuration.

    Session Lifecycle:
        create_session -> Creates container/StatefulSet + volume/PVC (stopped)
        start_session  -> Starts container/scales to 1, connects
        stop_session   -> Stops container/scales to 0 (preserves volume)
        delete_session -> Removes all resources including volume
        connect_session -> Attaches to running session
        list_sessions  -> Lists all sessions
        sync_session   -> Syncs files between local and remote
    """

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it).

        Creates the container/StatefulSet and volume/PVC but leaves it stopped.

        Args:
            config: Session configuration.

        Returns:
            Session object representing the created session.
        """
        ...

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources.

        Removes the container/StatefulSet and volume/PVC permanently.

        Args:
            name: Session name.
            confirm: Whether the user has confirmed deletion.

        Raises:
            ValueError: If session not found or confirm=False.
        """
        ...

    def start_session(
        self,
        name: str,
        sync: bool = True,
    ) -> int:
        """Start a session and connect to it.

        Starts the container/scales to 1, optionally syncs files, connects.

        Args:
            name: Session name.
            sync: Whether to sync workspace files before connecting.

        Returns:
            Exit code from the connected session.
        """
        ...

    def stop_session(self, name: str, sync: bool = False) -> None:
        """Stop a session (preserves volume).

        Stops the container/scales to 0 but keeps the volume intact.

        Args:
            name: Session name.
            sync: Whether to sync files back to local before stopping.
        """
        ...

    def connect_session(self, name: str) -> int:
        """Attach to a running session.

        Args:
            name: Session name.

        Returns:
            Exit code from the attached session.
        """
        ...

    def list_sessions(self) -> list[Session]:
        """List all sessions for current user.

        Returns:
            List of Session objects.
        """
        ...

    def sync_session(
        self,
        name: str,
        direction: str = "both",
    ) -> None:
        """Sync files between local and remote workspace.

        Args:
            name: Session name.
            direction: Sync direction ("local", "remote", "both").

        Note:
            For Podman backend with volume mounts, this may be a no-op
            or rsync for performance. For OpenShift, uses oc rsync.
        """
        ...

    def get_session(self, name: str) -> Session | None:
        """Get a session by name.

        Args:
            name: Session name.

        Returns:
            Session object or None if not found.
        """
        ...
