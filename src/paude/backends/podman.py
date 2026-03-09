"""Podman backend implementation."""

from __future__ import annotations

import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.base import Session, SessionConfig
from paude.backends.shared import SQUID_BLOCKED_LOG_PATH, decode_path, encode_path
from paude.constants import (
    CONTAINER_ENTRYPOINT,
    CONTAINER_WORKSPACE,
    GCP_ADC_FILENAME,
    GCP_ADC_SECRET_NAME,
    GCP_ADC_TARGET,
)
from paude.container.network import NetworkManager
from paude.container.runner import (
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    ContainerRunner,
)
from paude.container.volume import VolumeManager
from paude.environment import build_proxy_environment
from paude.platform import get_podman_machine_dns

PAUDE_LABEL_DOMAINS = "paude.io/allowed-domains"
PAUDE_LABEL_PROXY_IMAGE = "paude.io/proxy-image"


class SessionExistsError(Exception):
    """Session already exists."""

    pass


class SessionNotFoundError(Exception):
    """Session not found."""

    pass


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
        self._network_manager = NetworkManager()
        self._volume_manager = VolumeManager()

    def _container_name(self, session_name: str) -> str:
        """Get container name for a session."""
        return f"paude-{session_name}"

    def _volume_name(self, session_name: str) -> str:
        """Get volume name for a session."""
        return f"paude-{session_name}-workspace"

    def _proxy_container_name(self, session_name: str) -> str:
        """Get proxy container name for a session."""
        return f"paude-proxy-{session_name}"

    def _network_name(self, session_name: str) -> str:
        """Get internal network name for a session."""
        return f"paude-net-{session_name}"

    def _require_session(self, name: str) -> str:
        """Validate session exists and return its container name.

        Args:
            name: Session name.

        Returns:
            Container name for the session.

        Raises:
            SessionNotFoundError: If session not found.
        """
        container_name = self._container_name(name)
        if not self._runner.container_exists(container_name):
            raise SessionNotFoundError(f"Session '{name}' not found")
        return container_name

    def _require_running_session(self, name: str) -> str:
        """Validate session exists and is running, return its container name.

        Args:
            name: Session name.

        Returns:
            Container name for the session.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        container_name = self._require_session(name)
        if not self._runner.container_running(container_name):
            raise ValueError(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it."
            )
        return container_name

    def _find_container_by_session_name(self, name: str) -> dict[str, Any] | None:
        """Find a container by session name label.

        Args:
            name: Session name to search for.

        Returns:
            Container dict if found, None otherwise.
        """
        containers = self._runner.list_containers(label_filter=PAUDE_LABEL_APP)
        for container in containers:
            labels = container.get("Labels", {}) or {}
            if labels.get(PAUDE_LABEL_SESSION) == name:
                return container
        return None

    def _build_session_from_container(
        self, name: str, container: dict[str, Any]
    ) -> Session:
        """Build a Session object from a container dict.

        Args:
            name: Session name.
            container: Raw container dict from list_containers.

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
        status = self._check_proxy_health(name, labels, status)

        return Session(
            name=name,
            status=status,
            workspace=workspace,
            created_at=created_at,
            backend_type="podman",
            container_id=container.get("Id", ""),
            volume_name=self._volume_name(name),
        )

    def _has_proxy(self, session_name: str) -> bool:
        """Check if a session has a proxy container."""
        return self._runner.container_exists(self._proxy_container_name(session_name))

    def _ensure_gcp_adc_secret(self) -> str | None:
        """Create or replace the GCP ADC Podman secret.

        Returns:
            Secret spec string for --secret, or None if ADC file missing.
        """
        adc_path = Path.home() / ".config" / "gcloud" / GCP_ADC_FILENAME
        if not adc_path.is_file():
            return None

        self._runner.create_secret(GCP_ADC_SECRET_NAME, adc_path)

        return f"{GCP_ADC_SECRET_NAME},target={GCP_ADC_TARGET}"

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it).

        Creates the container, volume, and (if domain filtering is active)
        an internal network and proxy container. All resources are left stopped.

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
        use_proxy = config.allowed_domains is not None

        # Check if session already exists
        if self._runner.container_exists(container_name):
            raise SessionExistsError(f"Session '{session_name}' already exists")

        created_at = datetime.now(UTC).isoformat()

        # Create labels — persist allowed_domains and proxy_image for lifecycle
        labels: dict[str, str] = {
            "app": "paude",
            PAUDE_LABEL_SESSION: session_name,
            PAUDE_LABEL_WORKSPACE: encode_path(config.workspace, url_safe=True),
            PAUDE_LABEL_CREATED: created_at,
        }
        if use_proxy:
            labels[PAUDE_LABEL_DOMAINS] = ",".join(config.allowed_domains or [])
            if config.proxy_image:
                labels[PAUDE_LABEL_PROXY_IMAGE] = config.proxy_image

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        # Create volume for workspace persistence
        print(f"Creating volume {volume_name}...", file=sys.stderr)
        self._volume_manager.create_volume(volume_name, labels=labels)

        # Set up proxy network and container if domain filtering is active
        network_name: str | None = None
        if use_proxy:
            network_name = self._network_name(session_name)
            self._network_manager.create_internal_network(network_name)

            proxy_name = self._proxy_container_name(session_name)
            proxy_image = config.proxy_image
            if not proxy_image:
                raise ValueError("proxy_image is required when allowed_domains is set")

            dns = get_podman_machine_dns()
            print(f"Creating proxy {proxy_name}...", file=sys.stderr)
            try:
                self._runner.create_session_proxy(
                    name=proxy_name,
                    image=proxy_image,
                    network=network_name,
                    dns=dns,
                    allowed_domains=config.allowed_domains,
                )
            except Exception:
                self._network_manager.remove_network(network_name)
                self._volume_manager.remove_volume(volume_name, force=True)
                raise

        # Build mounts with session volume
        mounts = list(config.mounts)
        mounts.extend(["-v", f"{volume_name}:/pvc"])

        # Prepare environment
        env = dict(config.env)
        env["PAUDE_WORKSPACE"] = CONTAINER_WORKSPACE

        # Add proxy environment variables
        if use_proxy:
            proxy_name = self._proxy_container_name(session_name)
            env.update(build_proxy_environment(proxy_name))

        # Add YOLO flag to args if enabled
        claude_args = list(config.args)
        if config.yolo:
            claude_args = ["--dangerously-skip-permissions"] + claude_args

        # Store args in environment for entrypoint
        if claude_args:
            env["PAUDE_CLAUDE_ARGS"] = " ".join(claude_args)

        # Create GCP ADC secret (if credentials exist)
        secret_spec = self._ensure_gcp_adc_secret()
        secrets = [secret_spec] if secret_spec else None

        # Create container (stopped)
        print(f"Creating container {container_name}...", file=sys.stderr)
        try:
            self._runner.create_container(
                name=container_name,
                image=config.image,
                mounts=mounts,
                env=env,
                workdir="/pvc",
                labels=labels,
                entrypoint="sleep",
                command=["infinity"],
                secrets=secrets,
                network=network_name,
            )
        except Exception:
            # Cleanup all resources on failure
            if use_proxy:
                proxy_name = self._proxy_container_name(session_name)
                self._runner.remove_container(proxy_name, force=True)
                self._network_manager.remove_network(self._network_name(session_name))
            self._volume_manager.remove_volume(volume_name, force=True)
            self._runner.remove_secret(GCP_ADC_SECRET_NAME)
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

    def start_session_no_attach(self, name: str) -> None:
        """Start containers without attaching (for git setup, etc.).

        Starts the proxy (if present) and main container but does not
        attach or run the entrypoint.

        Args:
            name: Session name.

        Raises:
            SessionNotFoundError: If session not found.
        """
        container_name = self._require_session(name)
        if self._runner.container_running(container_name):
            return
        self._ensure_gcp_adc_secret()
        self._start_proxy_if_needed(name)
        self._runner.start_container(container_name)

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources.

        Removes the container, proxy, network, and volume permanently.

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
            if not self._volume_manager.volume_exists(volume_name):
                raise SessionNotFoundError(f"Session '{name}' not found")
            # Volume exists without container - still delete it
            print(f"Removing orphaned volume {volume_name}...", file=sys.stderr)
            self._volume_manager.remove_volume(volume_name, force=True)
            print(f"Session '{name}' deleted.", file=sys.stderr)
            return

        print(f"Deleting session '{name}'...", file=sys.stderr)

        # Stop container if running
        if self._runner.container_running(container_name):
            print(f"Stopping container {container_name}...", file=sys.stderr)
            self._runner.stop_container_graceful(container_name)

        # Stop and remove proxy container if it exists
        proxy_name = self._proxy_container_name(name)
        if self._runner.container_exists(proxy_name):
            print(f"Removing proxy {proxy_name}...", file=sys.stderr)
            self._runner.stop_container(proxy_name)
            self._runner.remove_container(proxy_name, force=True)

        # Remove main container
        print(f"Removing container {container_name}...", file=sys.stderr)
        self._runner.remove_container(container_name, force=True)

        # Remove network
        network_name = self._network_name(name)
        self._network_manager.remove_network(network_name)

        # Remove volume and secret
        print(f"Removing volume {volume_name}...", file=sys.stderr)
        self._volume_manager.remove_volume(volume_name, force=True)
        self._runner.remove_secret(GCP_ADC_SECRET_NAME)

        print(f"Session '{name}' deleted.", file=sys.stderr)

    def _get_proxy_config_from_labels(self, name: str) -> tuple[str, list[str]] | None:
        """Read proxy configuration from the main container's labels.

        Returns:
            Tuple of (proxy_image, domains) if proxy was configured,
            None if session has no proxy configuration.
        """
        container = self._find_container_by_session_name(name)
        if container is None:
            return None

        labels = container.get("Labels", {}) or {}

        domains_str = labels.get(PAUDE_LABEL_DOMAINS)
        if domains_str is None:
            return None  # No proxy configured

        proxy_image = labels.get(PAUDE_LABEL_PROXY_IMAGE, "")
        if not proxy_image:
            return None  # Can't recreate without image

        domains = [d for d in domains_str.split(",") if d]
        return (proxy_image, domains)

    def _start_proxy_if_needed(self, name: str) -> None:
        """Start or recreate the proxy container for a session.

        If the proxy container exists but is stopped, starts it.
        If the proxy container is missing but was expected (based on main
        container labels), recreates it from stored configuration.

        Args:
            name: Session name.
        """
        proxy_name = self._proxy_container_name(name)

        if self._runner.container_exists(proxy_name):
            if self._runner.container_running(proxy_name):
                return
            print(f"Starting proxy {proxy_name}...", file=sys.stderr)
            self._runner.start_session_proxy(proxy_name)
            return

        # Proxy doesn't exist — check if it was expected
        proxy_config = self._get_proxy_config_from_labels(name)
        if proxy_config is None:
            return  # No proxy expected for this session

        # Recreate the missing proxy
        proxy_image, domains = proxy_config
        network_name = self._network_name(name)

        # Ensure network exists (create_internal_network is idempotent)
        self._network_manager.create_internal_network(network_name)

        dns = get_podman_machine_dns()
        print(f"Recreating missing proxy {proxy_name}...", file=sys.stderr)
        self._runner.create_session_proxy(
            name=proxy_name,
            image=proxy_image,
            network=network_name,
            dns=dns,
            allowed_domains=domains,
        )
        self._runner.start_session_proxy(proxy_name)

    def _stop_proxy_if_needed(self, name: str) -> None:
        """Stop the proxy container for a session if one exists.

        Args:
            name: Session name.
        """
        proxy_name = self._proxy_container_name(name)
        if not self._runner.container_exists(proxy_name):
            return

        if not self._runner.container_running(proxy_name):
            return

        self._runner.stop_container(proxy_name)

    def start_session(self, name: str, github_token: str | None = None) -> int:
        """Start a session and connect to it.

        Starts the proxy (if present) and main container, then attaches.

        Args:
            name: Session name.
            github_token: Optional GitHub token to inject via podman exec env.
                Not stored in the container definition.

        Returns:
            Exit code from the connected session.

        Raises:
            SessionNotFoundError: If session not found.
        """
        container_name = self._require_session(name)

        state = self._runner.get_container_state(container_name)

        if state == "running":
            print(
                f"Session '{name}' is already running, connecting...",
                file=sys.stderr,
            )
            return self.connect_session(name, github_token=github_token)

        print(f"Starting session '{name}'...", file=sys.stderr)

        # Recreate GCP ADC secret with latest credentials
        self._ensure_gcp_adc_secret()

        # Start proxy before main container so it's ready for connections
        self._start_proxy_if_needed(name)

        # Start the main container
        self._runner.start_container(container_name)

        # Attach to the container via tmux entrypoint
        extra_env = {"GH_TOKEN": github_token} if github_token else None
        return self._runner.attach_container(
            container_name,
            entrypoint=CONTAINER_ENTRYPOINT,
            extra_env=extra_env,
        )

    def stop_session(self, name: str) -> None:
        """Stop a session (preserves volume).

        Stops the main container and proxy but keeps volumes intact.

        Args:
            name: Session name.
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

        # Stop proxy after main container
        self._stop_proxy_if_needed(name)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        """Attach to a running session.

        Args:
            name: Session name.
            github_token: Optional GitHub token to inject via podman exec env.
                Not stored in the container definition.

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

        # Ensure proxy is running (recreates if missing)
        self._start_proxy_if_needed(name)

        # Check if workspace is empty (no .git directory)
        check_result = self._runner.exec_in_container(
            container_name,
            ["test", "-d", "/pvc/workspace/.git"],
            check=False,
        )
        if check_result.returncode != 0:
            print("", file=sys.stderr)
            print("Workspace is empty. To sync code:", file=sys.stderr)
            print(f"  paude remote add {name}", file=sys.stderr)
            print(f"  git push paude-{name} main", file=sys.stderr)
            print("", file=sys.stderr)

        print(f"Connecting to session '{name}'...", file=sys.stderr)
        extra_env = {"GH_TOKEN": github_token} if github_token else None
        return self._runner.attach_container(
            container_name,
            entrypoint=CONTAINER_ENTRYPOINT,
            extra_env=extra_env,
        )

    def _check_proxy_health(
        self, session_name: str, labels: dict[str, str], status: str
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

        proxy_name = self._proxy_container_name(session_name)
        if not self._runner.container_exists(proxy_name):
            return "degraded"
        if not self._runner.container_running(proxy_name):
            return "degraded"

        return status

    def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of Session objects.
        """
        containers = self._runner.list_containers(label_filter=PAUDE_LABEL_APP)

        sessions = []
        for container in containers:
            labels = container.get("Labels", {}) or {}
            session_name = labels.get(PAUDE_LABEL_SESSION)
            if not session_name:
                continue

            sessions.append(self._build_session_from_container(session_name, container))

        return sessions

    def get_session(self, name: str) -> Session | None:
        """Get a session by name.

        Args:
            name: Session name.

        Returns:
            Session object or None if not found.
        """
        container = self._find_container_by_session_name(name)
        if container is None:
            return None

        return self._build_session_from_container(name, container)

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

    def get_allowed_domains(self, name: str) -> list[str] | None:
        """Get current allowed domains for a session.

        Reads the domains from the proxy container's ALLOWED_DOMAINS env var.
        Returns None if the session has no proxy (unrestricted network).

        Args:
            name: Session name.

        Returns:
            List of domains, or None if session has no proxy.

        Raises:
            SessionNotFoundError: If session not found.
        """
        self._require_session(name)

        proxy_name = self._proxy_container_name(name)
        if not self._runner.container_exists(proxy_name):
            return None  # No proxy = unrestricted

        domains_str = self._runner.get_container_env(proxy_name, "ALLOWED_DOMAINS")
        if not domains_str:
            return []

        return [d for d in domains_str.split(",") if d]

    def get_proxy_blocked_log(self, name: str) -> str | None:
        """Get raw squid blocked log from the proxy container.

        Returns:
            Raw log content, empty string if no blocks yet,
            or None if no proxy (unrestricted).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If proxy is not running.
        """
        self._require_session(name)

        proxy_name = self._proxy_container_name(name)
        if not self._runner.container_exists(proxy_name):
            return None

        if not self._runner.container_running(proxy_name):
            raise ValueError(f"Proxy for session '{name}' is not running.")

        result = self._runner.exec_in_container(
            proxy_name, ["cat", SQUID_BLOCKED_LOG_PATH], check=False
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def update_allowed_domains(self, name: str, domains: list[str]) -> None:
        """Update allowed domains for a session.

        Recreates the proxy container with the new domain list.

        Args:
            name: Session name.
            domains: New list of allowed domains.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session has no proxy deployment.
        """
        self._require_session(name)

        proxy_name = self._proxy_container_name(name)
        if not self._runner.container_exists(proxy_name):
            raise ValueError(
                f"Session '{name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        # Get proxy image from the proxy container
        result = subprocess.run(
            ["podman", "inspect", "-f", "{{.ImageName}}", proxy_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"Cannot inspect proxy container: {result.stderr}")
        proxy_image = result.stdout.strip()

        network_name = self._network_name(name)
        dns = get_podman_machine_dns()

        print(f"Updating proxy domains for session '{name}'...", file=sys.stderr)
        self._runner.recreate_session_proxy(
            name=proxy_name,
            image=proxy_image,
            network=network_name,
            dns=dns,
            allowed_domains=domains,
        )

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        """Execute a command inside a running session's container.

        Args:
            name: Session name.
            command: Shell command to execute.

        Returns:
            Tuple of (return_code, stdout, stderr).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        container_name = self._require_running_session(name)

        result = self._runner.exec_in_container(
            container_name, ["bash", "-c", command], check=False
        )
        return (result.returncode, result.stdout, result.stderr)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        """Copy a file or directory from local to a running session.

        Args:
            name: Session name.
            local_path: Local file or directory path.
            remote_path: Destination path inside the container.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        container_name = self._require_running_session(name)

        subprocess.run(
            ["podman", "cp", local_path, f"{container_name}:{remote_path}"],
            check=True,
        )

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        """Copy a file or directory from a running session to local.

        Args:
            name: Session name.
            remote_path: Source path inside the container.
            local_path: Local destination path.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        container_name = self._require_running_session(name)

        subprocess.run(
            ["podman", "cp", f"{container_name}:{remote_path}", local_path],
            check=True,
        )

    def stop_container(self, name: str) -> None:
        """Stop a container by name.

        Args:
            name: Container name.
        """
        self._runner.stop_container(name)
