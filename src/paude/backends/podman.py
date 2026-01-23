"""Podman backend implementation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from paude.backends.base import Session
from paude.container.runner import ContainerRunner


class PodmanBackend:
    """Podman container backend.

    This backend runs containers locally using Podman. Sessions are synchronous
    and ephemeral - they run in the foreground and exit when Claude exits.
    """

    def __init__(self) -> None:
        """Initialize the Podman backend."""
        self._runner = ContainerRunner()
        self._current_session: Session | None = None

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
        network: str | None = None,
    ) -> Session:
        """Start a new Claude session.

        For Podman, this runs synchronously - the session blocks until Claude exits.

        Args:
            image: Container image to use.
            workspace: Local workspace path.
            env: Environment variables to set.
            mounts: Volume mount arguments.
            args: Arguments to pass to Claude.
            workdir: Working directory inside container.
            network_restricted: Whether to restrict network (default True).
            yolo: Enable YOLO mode (skip permission prompts).
            network: Network name to use (for proxy setup).

        Returns:
            Session object with exit code in status.
        """
        import secrets
        import time

        session_id = f"{int(time.time())}-{secrets.token_hex(4)}"
        created_at = datetime.now(UTC).isoformat()

        self._current_session = Session(
            id=session_id,
            status="running",
            workspace=workspace,
            created_at=created_at,
            backend_type="podman",
        )

        # Run Claude (blocks until exit)
        exit_code = self._runner.run_claude(
            image=image,
            mounts=mounts,
            env=env,
            args=args,
            workdir=workdir or str(workspace),
            network=network,
            yolo=yolo,
            allow_network=not network_restricted,
        )

        # Update session status
        self._current_session = Session(
            id=session_id,
            status="stopped" if exit_code == 0 else "error",
            workspace=workspace,
            created_at=created_at,
            backend_type="podman",
        )

        return self._current_session

    def attach_session(self, session_id: str) -> int:
        """Attach to a running session.

        For Podman, sessions are ephemeral and cannot be reattached.
        This returns an error code.

        Args:
            session_id: ID of the session to attach to.

        Returns:
            Exit code (always 1 for Podman - sessions are ephemeral).
        """
        import sys

        print(
            "Podman sessions are ephemeral and cannot be reattached. "
            "Run 'paude' to start a new session.",
            file=sys.stderr,
        )
        return 1

    def stop_session(self, session_id: str) -> None:
        """Stop and cleanup a session.

        For Podman, sessions are managed by the container runtime.
        The container is removed automatically when it exits (--rm flag).

        Args:
            session_id: ID of the session to stop.
        """
        pass

    def list_sessions(self) -> list[Session]:
        """List all sessions for current user.

        For Podman, sessions are ephemeral. Returns empty list.

        Returns:
            Empty list (Podman sessions are not persistent).
        """
        return []

    def sync_workspace(
        self,
        session_id: str,
        direction: str = "both",
    ) -> None:
        """Sync files between local and remote workspace.

        For Podman, this is a no-op since volumes are mounted directly.

        Args:
            session_id: ID of the session.
            direction: Sync direction (ignored for Podman).
        """
        pass

    def run_proxy(
        self,
        image: str,
        network: str,
        dns: str | None = None,
    ) -> str:
        """Start the proxy container.

        Args:
            image: Proxy image to run.
            network: Network to attach to.
            dns: Optional DNS IP for squid to use.

        Returns:
            Container name.
        """
        return self._runner.run_proxy(image, network, dns)

    def stop_container(self, name: str) -> None:
        """Stop a container by name.

        Args:
            name: Container name.
        """
        self._runner.stop_container(name)

    def run_post_create(
        self,
        image: str,
        mounts: list[str],
        env: dict[str, str],
        command: str,
        workdir: str,
        network: str | None = None,
    ) -> bool:
        """Run the postCreateCommand.

        Args:
            image: Container image to use.
            mounts: Volume mount arguments.
            env: Environment variables.
            command: Command to run.
            workdir: Working directory for the command.
            network: Optional network.

        Returns:
            True if successful.
        """
        return self._runner.run_post_create(
            image=image,
            mounts=mounts,
            env=env,
            command=command,
            workdir=workdir,
            network=network,
        )
