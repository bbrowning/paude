"""Podman backend implementation."""

from __future__ import annotations

import base64
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

from paude.backends.base import Session, SessionConfig
from paude.container.runner import (
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    ContainerRunner,
)


class SessionExistsError(Exception):
    """Session already exists."""

    pass


class SessionNotFoundError(Exception):
    """Session not found."""

    pass


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


def _encode_path(path: Path) -> str:
    """Encode a path for use in labels.

    Args:
        path: Path to encode.

    Returns:
        Base64-encoded path string.
    """
    return base64.urlsafe_b64encode(str(path).encode()).decode()


def _decode_path(encoded: str) -> Path:
    """Decode a path from label value.

    Args:
        encoded: Base64-encoded path string.

    Returns:
        Decoded Path object.
    """
    try:
        return Path(base64.urlsafe_b64decode(encoded.encode()).decode())
    except Exception:
        return Path(encoded)


class PodmanBackend:
    """Podman container backend with persistent sessions.

    This backend runs containers locally using Podman. Sessions use named
    volumes for persistence and can be started/stopped/resumed.

    Session resources:
        - Container: paude-{session-name}
        - Volume: paude-{session-name}-workspace
    """

    def __init__(self) -> None:
        """Initialize the Podman backend."""
        self._runner = ContainerRunner()
        self._current_session: Session | None = None

    def _container_name(self, session_name: str) -> str:
        """Get container name for a session."""
        return f"paude-{session_name}"

    def _volume_name(self, session_name: str) -> str:
        """Get volume name for a session."""
        return f"paude-{session_name}-workspace"

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it).

        Creates the container and volume but leaves the container stopped.

        Args:
            config: Session configuration.

        Returns:
            Session object representing the created session.

        Raises:
            SessionExistsError: If session with this name already exists.
        """
        # Generate session name if not provided
        session_name = config.name or _generate_session_name(config.workspace)

        container_name = self._container_name(session_name)
        volume_name = self._volume_name(session_name)

        # Check if session already exists
        if self._runner.container_exists(container_name):
            raise SessionExistsError(f"Session '{session_name}' already exists")

        created_at = datetime.now(UTC).isoformat()

        # Create labels
        labels = {
            "app": "paude",
            PAUDE_LABEL_SESSION: session_name,
            PAUDE_LABEL_WORKSPACE: _encode_path(config.workspace),
            PAUDE_LABEL_CREATED: created_at,
        }

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        # Create volume for workspace persistence
        print(f"Creating volume {volume_name}...", file=sys.stderr)
        self._runner.create_volume(volume_name, labels=labels)

        # Build mounts with session volume
        mounts = list(config.mounts)
        # Add the session volume for /pvc
        mounts.extend(["-v", f"{volume_name}:/pvc"])

        # Prepare environment
        env = dict(config.env)

        # Add YOLO flag to args if enabled
        claude_args = list(config.args)
        if config.yolo:
            claude_args = ["--dangerously-skip-permissions"] + claude_args

        # Store args in environment for entrypoint
        if claude_args:
            env["PAUDE_CLAUDE_ARGS"] = " ".join(claude_args)

        # Create container (stopped)
        # Use sleep infinity as entrypoint to keep container alive
        # The actual session setup happens when attaching via exec
        print(f"Creating container {container_name}...", file=sys.stderr)
        try:
            self._runner.create_container(
                name=container_name,
                image=config.image,
                mounts=mounts,
                env=env,
                workdir=config.workdir or str(config.workspace),
                labels=labels,
                entrypoint="sleep",
                command=["infinity"],
            )
        except Exception:
            # Cleanup volume on failure
            self._runner.remove_volume(volume_name, force=True)
            raise

        print(f"Session '{session_name}' created (stopped).", file=sys.stderr)

        return Session(
            name=session_name,
            status="stopped",
            workspace=config.workspace,
            created_at=created_at,
            backend_type="podman",
            container_id=container_name,
            volume_name=volume_name,
        )

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources.

        Removes the container and volume permanently.

        Args:
            name: Session name.
            confirm: Whether the user has confirmed deletion.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If confirm=False.
        """
        if not confirm:
            raise ValueError(
                "Deletion requires confirmation. Pass confirm=True or use --confirm."
            )

        container_name = self._container_name(name)
        volume_name = self._volume_name(name)

        # Check if session exists
        if not self._runner.container_exists(container_name):
            if not self._runner.volume_exists(volume_name):
                raise SessionNotFoundError(f"Session '{name}' not found")
            # Volume exists without container - still delete it
            print(f"Removing orphaned volume {volume_name}...", file=sys.stderr)
            self._runner.remove_volume(volume_name, force=True)
            print(f"Session '{name}' deleted.", file=sys.stderr)
            return

        print(f"Deleting session '{name}'...", file=sys.stderr)

        # Stop container if running
        if self._runner.container_running(container_name):
            print(f"Stopping container {container_name}...", file=sys.stderr)
            self._runner.stop_container_graceful(container_name)

        # Remove container
        print(f"Removing container {container_name}...", file=sys.stderr)
        self._runner.remove_container(container_name, force=True)

        # Remove volume
        print(f"Removing volume {volume_name}...", file=sys.stderr)
        self._runner.remove_volume(volume_name, force=True)

        print(f"Session '{name}' deleted.", file=sys.stderr)

    def start_session(self, name: str, sync: bool = True) -> int:
        """Start a session and connect to it.

        Starts the container and attaches to it via tmux.

        Args:
            name: Session name.
            sync: Whether to sync workspace files (for Podman, volumes are
                  live-mounted so this is typically not needed).

        Returns:
            Exit code from the connected session.

        Raises:
            SessionNotFoundError: If session not found.
        """
        container_name = self._container_name(name)

        if not self._runner.container_exists(container_name):
            raise SessionNotFoundError(f"Session '{name}' not found")

        state = self._runner.get_container_state(container_name)

        if state == "running":
            print(
                f"Session '{name}' is already running, connecting...",
                file=sys.stderr,
            )
            return self.connect_session(name)

        print(f"Starting session '{name}'...", file=sys.stderr)

        # Start the container
        self._runner.start_container(container_name)

        # Attach to the container via tmux entrypoint
        return self._runner.attach_container(
            container_name,
            entrypoint="/usr/local/bin/entrypoint.sh",
        )

    def stop_session(self, name: str, sync: bool = False) -> None:
        """Stop a session (preserves volume).

        Stops the container but keeps the volume intact.

        Args:
            name: Session name.
            sync: Whether to sync files back to local (no-op for Podman
                  with direct volume mounts).
        """
        container_name = self._container_name(name)

        if not self._runner.container_exists(container_name):
            print(f"Session '{name}' not found.", file=sys.stderr)
            return

        if not self._runner.container_running(container_name):
            print(f"Session '{name}' is already stopped.", file=sys.stderr)
            return

        print(f"Stopping session '{name}'...", file=sys.stderr)
        self._runner.stop_container_graceful(container_name)
        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(self, name: str) -> int:
        """Attach to a running session.

        Args:
            name: Session name.

        Returns:
            Exit code from the attached session.
        """
        container_name = self._container_name(name)

        if not self._runner.container_exists(container_name):
            print(f"Session '{name}' not found.", file=sys.stderr)
            return 1

        if not self._runner.container_running(container_name):
            print(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it.",
                file=sys.stderr,
            )
            return 1

        print(f"Connecting to session '{name}'...", file=sys.stderr)
        return self._runner.attach_container(
            container_name,
            entrypoint="/usr/local/bin/entrypoint.sh",
        )

    def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of Session objects.
        """
        # Find all paude containers
        containers = self._runner.list_containers(label_filter=PAUDE_LABEL_APP)

        sessions = []
        for container in containers:
            labels = container.get("Labels", {}) or {}

            session_name = labels.get(PAUDE_LABEL_SESSION)
            if not session_name:
                continue

            workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
            workspace = (
                _decode_path(workspace_encoded)
                if workspace_encoded
                else Path("/")
            )
            created_at = labels.get(PAUDE_LABEL_CREATED, "")

            # Map container state to session status
            state = container.get("State", "unknown")
            status_map = {
                "running": "running",
                "exited": "stopped",
                "stopped": "stopped",
                "created": "stopped",
                "paused": "stopped",
                "dead": "error",
            }
            status = status_map.get(state.lower(), "error")

            sessions.append(Session(
                name=session_name,
                status=status,
                workspace=workspace,
                created_at=created_at,
                backend_type="podman",
                container_id=container.get("Id", ""),
                volume_name=self._volume_name(session_name),
            ))

        return sessions

    def sync_session(
        self,
        name: str,
        direction: str = "both",
    ) -> None:
        """Sync files between local and remote workspace.

        For Podman with direct volume mounts, this is typically a no-op
        since files are already synchronized via the mount.

        Args:
            name: Session name.
            direction: Sync direction ("local", "remote", "both").
        """
        # For Podman with direct volume mounts, sync is not needed
        # The workspace is mounted directly from the host
        print(
            "Podman sessions use direct volume mounts - "
            "files are already synchronized.",
            file=sys.stderr,
        )

    def get_session(self, name: str) -> Session | None:
        """Get a session by name.

        Args:
            name: Session name.

        Returns:
            Session object or None if not found.
        """
        container_name = self._container_name(name)

        if not self._runner.container_exists(container_name):
            return None

        # Get container info
        containers = self._runner.list_containers(label_filter=PAUDE_LABEL_APP)
        for container in containers:
            labels = container.get("Labels", {}) or {}
            if labels.get(PAUDE_LABEL_SESSION) == name:
                workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
                workspace = (
                _decode_path(workspace_encoded)
                if workspace_encoded
                else Path("/")
            )
                created_at = labels.get(PAUDE_LABEL_CREATED, "")

                state = container.get("State", "unknown")
                status_map = {
                    "running": "running",
                    "exited": "stopped",
                    "stopped": "stopped",
                    "created": "stopped",
                    "paused": "stopped",
                    "dead": "error",
                }
                status = status_map.get(state.lower(), "error")

                return Session(
                    name=name,
                    status=status,
                    workspace=workspace,
                    created_at=created_at,
                    backend_type="podman",
                    container_id=container.get("Id", ""),
                    volume_name=self._volume_name(name),
                )

        return None

    def find_session_for_workspace(self, workspace: Path) -> Session | None:
        """Find an existing session for a workspace.

        Args:
            workspace: Workspace path.

        Returns:
            Session object or None if no session exists for this workspace.
        """
        sessions = self.list_sessions()
        workspace_resolved = workspace.resolve()

        for session in sessions:
            if session.workspace.resolve() == workspace_resolved:
                return session

        return None

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
