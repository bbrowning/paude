"""Podman/Docker backend implementation."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from paude.backends.base import Session, SessionConfig
from paude.backends.podman.exceptions import (
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.podman.helpers import (
    _generate_session_name,
    build_session_from_container,
    container_name,
    find_container_by_session_name,
    network_name,
    proxy_container_name,
    volume_name,
)
from paude.backends.podman.proxy import PodmanProxyManager
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    build_session_env,
    encode_path,
)
from paude.constants import (
    CONTAINER_ENTRYPOINT,
    CONTAINER_WORKSPACE,
    GCP_ADC_FILENAME,
    GCP_ADC_SECRET_NAME,
    GCP_ADC_TARGET,
)
from paude.container.engine import ContainerEngine
from paude.container.network import NetworkManager
from paude.container.runner import ContainerRunner
from paude.container.volume import VolumeManager


class PodmanBackend:
    """Local container backend (Podman or Docker) with persistent sessions.

    This backend runs containers locally using Podman or Docker. Sessions use
    named volumes for persistence and can be started/stopped/resumed.

    Session resources:
        - Container: paude-{session-name}
        - Volume: paude-{session-name}-workspace
    """

    def __init__(self, engine: ContainerEngine | None = None) -> None:
        """Initialize the backend.

        Args:
            engine: Container engine to use. Defaults to Podman.
        """
        self._engine = engine or ContainerEngine()
        self._runner = ContainerRunner(self._engine)
        self._network_manager = NetworkManager(self._engine)
        self._volume_manager = VolumeManager(self._engine)
        self._proxy = PodmanProxyManager(self._runner, self._network_manager)

    @property
    def engine(self) -> ContainerEngine:
        """Access the underlying container engine."""
        return self._engine

    @property
    def backend_type(self) -> str:
        """Backend type string for Session objects."""
        return self._engine.binary

    def _require_session(self, name: str) -> str:
        """Validate session exists and return its container name."""
        cname = container_name(name)
        if not self._runner.container_exists(cname):
            raise SessionNotFoundError(f"Session '{name}' not found")
        return cname

    def _require_running_session(self, name: str) -> str:
        """Validate session exists and is running, return its container name."""
        cname = self._require_session(name)
        if not self._runner.container_running(cname):
            raise ValueError(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it."
            )
        return cname

    def _build_attach_env(
        self, name: str, github_token: str | None
    ) -> dict[str, str] | None:
        """Build extra environment for container attachment."""
        from paude.agents import get_agent
        from paude.agents.base import build_secret_environment_from_config

        container = find_container_by_session_name(self._runner, name)
        labels = (container.get("Labels", {}) or {}) if container else {}
        agent_name = labels.get(PAUDE_LABEL_AGENT, "claude")
        agent = get_agent(agent_name)
        secret_env = build_secret_environment_from_config(agent.config)

        extra_env: dict[str, str] = {}
        if github_token:
            extra_env["GH_TOKEN"] = github_token
        extra_env.update(secret_env)
        return extra_env or None

    @staticmethod
    def _local_adc_path() -> Path | None:
        """Return the local GCP ADC file path, or None if it doesn't exist."""
        path = Path.home() / ".config" / "gcloud" / GCP_ADC_FILENAME
        return path if path.is_file() else None

    def _ensure_gcp_credentials(self) -> list[str] | None:
        """Ensure GCP ADC credentials are available via Podman secret.

        For Podman: creates a podman secret and returns a secret spec.
        For Docker: credentials are injected via ``_inject_credentials``
        after the container is started.

        Returns:
            List of secret specs for --secret, or None.
        """
        if not self._engine.supports_secrets:
            return None

        adc_path = self._local_adc_path()
        if adc_path is None:
            return None

        self._runner.create_secret(GCP_ADC_SECRET_NAME, adc_path)
        secret_spec = f"{GCP_ADC_SECRET_NAME},target={GCP_ADC_TARGET}"
        return [secret_spec]

    def _inject_credentials(self, cname: str) -> None:
        """Inject GCP ADC credentials into a running container.

        For Docker (no standalone secrets): reads the local ADC file and
        pipes it into the container via ``exec``.  Nothing is written to
        the host filesystem, which is critical for SSH remotes where
        other users share the host.

        For Podman: no-op (secrets are handled at create time).
        """
        if self._engine.supports_secrets:
            return

        adc_path = self._local_adc_path()
        if adc_path is None:
            return

        content = adc_path.read_text()
        self._runner.inject_file(cname, content, GCP_ADC_TARGET, owner="paude:0")

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it).

        Raises:
            SessionExistsError: If session with this name already exists.
        """
        session_name = config.name or _generate_session_name(config.workspace)

        cname = container_name(session_name)
        vname = volume_name(session_name)
        use_proxy = config.allowed_domains is not None

        if self._runner.container_exists(cname):
            raise SessionExistsError(f"Session '{session_name}' already exists")

        created_at = datetime.now(UTC).isoformat()

        # Create labels
        labels: dict[str, str] = {
            "app": "paude",
            PAUDE_LABEL_SESSION: session_name,
            PAUDE_LABEL_WORKSPACE: encode_path(config.workspace, url_safe=True),
            PAUDE_LABEL_CREATED: created_at,
            PAUDE_LABEL_AGENT: config.agent,
        }
        if use_proxy:
            labels[PAUDE_LABEL_DOMAINS] = ",".join(config.allowed_domains or [])
            if config.proxy_image:
                labels[PAUDE_LABEL_PROXY_IMAGE] = config.proxy_image

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        # Create volume for workspace persistence
        print(f"Creating volume {vname}...", file=sys.stderr)
        self._volume_manager.create_volume(vname, labels=labels)

        # Set up proxy network and container if domain filtering is active
        network: str | None = None
        if use_proxy:
            try:
                network = self._proxy.create_proxy(
                    session_name, config.proxy_image or "", config.allowed_domains
                )
            except Exception:
                self._volume_manager.remove_volume(vname, force=True)
                raise

        # Build mounts with session volume
        mounts = list(config.mounts)
        mounts.extend(["-v", f"{vname}:/pvc"])

        # Prepare environment
        from paude.agents import get_agent

        agent = get_agent(config.agent)
        proxy_name_for_env = proxy_container_name(session_name) if use_proxy else None
        env, _agent_args = build_session_env(
            config, agent, proxy_name=proxy_name_for_env
        )
        env["PAUDE_WORKSPACE"] = CONTAINER_WORKSPACE

        # Ensure GCP credentials (Podman secrets; Docker injects after start)
        secrets = self._ensure_gcp_credentials()

        # Create container (stopped)
        print(f"Creating container {cname}...", file=sys.stderr)
        try:
            self._runner.create_container(
                name=cname,
                image=config.image,
                mounts=mounts,
                env=env,
                workdir="/pvc",
                labels=labels,
                entrypoint="sleep",
                command=["infinity"],
                secrets=secrets,
                network=network,
                gpu=config.gpu,
            )
        except Exception:
            # Cleanup all resources on failure
            if use_proxy:
                pname = proxy_container_name(session_name)
                self._runner.remove_container(pname, force=True)
                self._network_manager.remove_network(network_name(session_name))
            self._volume_manager.remove_volume(vname, force=True)
            self._runner.remove_secret(GCP_ADC_SECRET_NAME)
            raise

        print(f"Session '{session_name}' created (stopped).", file=sys.stderr)

        return Session(
            name=session_name,
            status="stopped",
            workspace=config.workspace,
            created_at=created_at,
            backend_type=self.backend_type,
            container_id=cname,
            volume_name=vname,
            agent=config.agent,
        )

    def _fix_volume_permissions(self, container_name: str) -> None:
        """Fix /pvc volume ownership for Docker.

        Docker volumes are root-owned by default, unlike Podman which uses
        user namespaces. Run chown as root so the paude user can write.
        """
        if self._engine.supports_secrets:
            return  # Podman handles this via user namespaces

        self._engine.run(
            "exec",
            "--user",
            "root",
            container_name,
            "chown",
            "paude:0",
            "/pvc",
            check=False,
        )

    def start_session_no_attach(self, name: str) -> None:
        """Start containers without attaching (for git setup, etc.)."""
        cname = self._require_session(name)
        if self._runner.container_running(cname):
            return
        self._ensure_gcp_credentials()
        self._proxy.start_if_needed(name)
        self._runner.start_container(cname)
        self._fix_volume_permissions(cname)
        self._inject_credentials(cname)

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources."""
        if not confirm:
            raise ValueError(
                "Deletion requires confirmation. Pass confirm=True or use --confirm."
            )

        cname = container_name(name)
        vname = volume_name(name)

        if not self._runner.container_exists(cname):
            if not self._volume_manager.volume_exists(vname):
                raise SessionNotFoundError(f"Session '{name}' not found")
            print(f"Removing orphaned volume {vname}...", file=sys.stderr)
            self._volume_manager.remove_volume_verified(vname)
            return

        print(f"Deleting session '{name}'...", file=sys.stderr)

        if self._runner.container_running(cname):
            print(f"Stopping container {cname}...", file=sys.stderr)
            self._runner.stop_container_graceful(cname)

        # Stop and remove proxy container if it exists
        pname = proxy_container_name(name)
        if self._runner.container_exists(pname):
            print(f"Removing proxy {pname}...", file=sys.stderr)
            self._runner.stop_container(pname)
            self._runner.remove_container_verified(pname)

        # Remove main container
        print(f"Removing container {cname}...", file=sys.stderr)
        self._runner.remove_container_verified(cname)

        # Remove network
        self._network_manager.remove_network(network_name(name))

        # Remove volume and secret
        print(f"Removing volume {vname}...", file=sys.stderr)
        self._volume_manager.remove_volume_verified(vname)
        self._runner.remove_secret(GCP_ADC_SECRET_NAME)

    def start_session(self, name: str, github_token: str | None = None) -> int:
        """Start a session and connect to it."""
        cname = self._require_session(name)

        state = self._runner.get_container_state(cname)

        if state == "running":
            print(
                f"Session '{name}' is already running, connecting...",
                file=sys.stderr,
            )
            return self.connect_session(name, github_token=github_token)

        print(f"Starting session '{name}'...", file=sys.stderr)

        self._ensure_gcp_credentials()
        self._proxy.start_if_needed(name)
        self._runner.start_container(cname)
        self._fix_volume_permissions(cname)
        self._inject_credentials(cname)

        return self._runner.attach_container(
            cname,
            entrypoint=CONTAINER_ENTRYPOINT,
            extra_env=self._build_attach_env(name, github_token),
        )

    def stop_session(self, name: str) -> None:
        """Stop a session (preserves volume)."""
        cname = container_name(name)

        if not self._runner.container_exists(cname):
            print(f"Session '{name}' not found.", file=sys.stderr)
            return

        if not self._runner.container_running(cname):
            print(f"Session '{name}' is already stopped.", file=sys.stderr)
            return

        print(f"Stopping session '{name}'...", file=sys.stderr)
        self._runner.stop_container_graceful(cname)

        self._proxy.stop_if_needed(name)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        """Attach to a running session."""
        cname = container_name(name)

        if not self._runner.container_exists(cname):
            print(f"Session '{name}' not found.", file=sys.stderr)
            return 1

        if not self._runner.container_running(cname):
            print(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it.",
                file=sys.stderr,
            )
            return 1

        # Ensure proxy is running (recreates if missing)
        self._proxy.start_if_needed(name)

        # Check if workspace is empty (no .git directory)
        check_result = self._runner.exec_in_container(
            cname,
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
        return self._runner.attach_container(
            cname,
            entrypoint=CONTAINER_ENTRYPOINT,
            extra_env=self._build_attach_env(name, github_token),
        )

    def list_sessions(self) -> list[Session]:
        """List all sessions."""
        containers = self._runner.list_containers(label_filter=PAUDE_LABEL_APP)

        sessions = []
        for c in containers:
            labels = c.get("Labels", {}) or {}
            session_name = labels.get(PAUDE_LABEL_SESSION)
            if not session_name:
                continue

            sessions.append(
                build_session_from_container(
                    session_name, c, self._runner, backend_type=self.backend_type
                )
            )

        return sessions

    def get_session(self, name: str) -> Session | None:
        """Get a session by name."""
        container = find_container_by_session_name(self._runner, name)
        if container is None:
            return None

        return build_session_from_container(
            name, container, self._runner, backend_type=self.backend_type
        )

    def find_session_for_workspace(self, workspace: Path) -> Session | None:
        """Find an existing session for a workspace."""
        sessions = self.list_sessions()
        workspace_resolved = workspace.resolve()

        for session in sessions:
            if session.workspace.resolve() == workspace_resolved:
                return session

        return None

    def get_allowed_domains(self, name: str) -> list[str] | None:
        """Get current allowed domains for a session."""
        self._require_session(name)
        return self._proxy.get_allowed_domains(name)

    def get_proxy_blocked_log(self, name: str) -> str | None:
        """Get raw squid blocked log from the proxy container."""
        self._require_session(name)
        return self._proxy.get_blocked_log(name)

    def update_allowed_domains(self, name: str, domains: list[str]) -> None:
        """Update allowed domains for a session."""
        self._require_session(name)
        self._proxy.update_domains(name, domains)

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        """Execute a command inside a running session's container."""
        cname = self._require_running_session(name)

        result = self._runner.exec_in_container(
            cname, ["bash", "-c", command], check=False
        )
        return (result.returncode, result.stdout, result.stderr)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        """Copy a file or directory from local to a running session."""
        cname = self._require_running_session(name)
        self._engine.run("cp", local_path, f"{cname}:{remote_path}")

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        """Copy a file or directory from a running session to local."""
        cname = self._require_running_session(name)
        self._engine.run("cp", f"{cname}:{remote_path}", local_path)

    def stop_container(self, name: str) -> None:
        """Stop a container by name."""
        self._runner.stop_container(name)
