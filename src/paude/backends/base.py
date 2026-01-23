"""Base protocol for container backends."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Session:
    """Represents a paude session.

    Attributes:
        id: Unique session identifier.
        status: Session status ("running", "stopped", "error", "pending").
        workspace: Local workspace path.
        created_at: ISO timestamp of session creation.
        backend_type: Backend type ("podman" or "openshift").
    """

    id: str
    status: str
    workspace: Path
    created_at: str
    backend_type: str


class Backend(Protocol):
    """Container backend interface.

    All container backends (Podman, OpenShift) must implement this protocol.
    The CLI delegates to the appropriate backend based on configuration.
    """

    def start_session(
        self,
        image: str,
        workspace: Path,
        env: dict[str, str],
        mounts: list[str],
        args: list[str],
        workdir: str | None = None,
        network_restricted: bool = True,
        yolo: bool = False,
    ) -> Session:
        """Start a new Claude session.

        Args:
            image: Container image to use.
            workspace: Local workspace path.
            env: Environment variables to set.
            mounts: Volume mount arguments (Podman-style).
            args: Arguments to pass to Claude.
            workdir: Working directory inside container.
            network_restricted: Whether to restrict network (default True).
            yolo: Enable YOLO mode (skip permission prompts).

        Returns:
            Session object representing the started session.
        """
        ...

    def attach_session(self, session_id: str) -> int:
        """Attach to a running session.

        Args:
            session_id: ID of the session to attach to.

        Returns:
            Exit code from the attached session.
        """
        ...

    def stop_session(self, session_id: str) -> None:
        """Stop and cleanup a session.

        Args:
            session_id: ID of the session to stop.
        """
        ...

    def list_sessions(self) -> list[Session]:
        """List all sessions for current user.

        Returns:
            List of Session objects.
        """
        ...

    def sync_workspace(
        self,
        session_id: str,
        direction: str = "both",
    ) -> None:
        """Sync files between local and remote workspace.

        Args:
            session_id: ID of the session.
            direction: Sync direction ("local", "remote", "both").

        Note:
            For Podman backend, this is a no-op since volumes are mounted directly.
            For OpenShift backend, this triggers file synchronization.
        """
        ...
