"""Podman/Docker backend implementation."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from paude.agents.base import AgentComposition
from paude.backends.base import Session, SessionConfig
from paude.backends.labels import (
    PAUDE_LABEL_APP,
    PAUDE_LABEL_SESSION,
    SessionSpec,
)
from paude.backends.podman.exceptions import (
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.podman.file_copy import (
    copy_from_container,
    copy_to_container,
)
from paude.backends.podman.helpers import (
    _generate_session_name,
    build_session_from_container,
    composition_for_spec,
    container_name,
    find_container_by_session_name,
    get_session_agent,
    get_session_composition,
    proxy_container_name,
    require_running_session,
    require_session,
    volume_name,
)
from paude.backends.podman.port_forward import PodmanPortForwardManager
from paude.backends.podman.proxy import PodmanProxyManager
from paude.backends.podman.resources import SessionResources
from paude.backends.podman.session_setup import SessionSetup
from paude.backends.port_forward_utils import ForwardPort, merge_forward_ports
from paude.constants import (
    CONTAINER_ENTRYPOINT,
)
from paude.container.engine import ContainerEngine
from paude.container.network import NetworkManager
from paude.container.runner import ContainerRunner
from paude.container.volume import VolumeManager

if TYPE_CHECKING:
    from paude.agents.base import Agent


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
        self._port_forward = PodmanPortForwardManager(self._engine)
        self._setup = SessionSetup(self._runner, self._engine)
        self._resources = SessionResources(
            self._runner, self._network_manager, self._volume_manager, self._proxy
        )

    @property
    def engine(self) -> ContainerEngine:
        """Access the underlying container engine."""
        return self._engine

    @property
    def resources(self) -> SessionResources:
        """Read and remove this session's podman objects.

        The public seam for callers that drive a rebuild (upgrade, backup)
        without needing -- or being handed -- the backend's collaborators.
        """
        return self._resources

    @property
    def backend_type(self) -> str:
        """Backend type string for Session objects."""
        return self._engine.binary

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it)."""
        name = config.name or _generate_session_name(config.workspace)
        cname = container_name(name)
        vname = volume_name(name)
        if self._runner.container_exists(cname):
            raise SessionExistsError(f"Session '{name}' already exists")

        created_at = datetime.now(UTC).isoformat()
        labels = SessionSetup.build_session_labels(config, name, created_at)
        print(f"Creating session '{name}'...", file=sys.stderr)
        volume_reused = config.reuse_volume and self._volume_manager.volume_exists(
            vname
        )
        if volume_reused:
            print(f"Reusing existing volume {vname}...", file=sys.stderr)
        else:
            print(f"Creating volume {vname}...", file=sys.stderr)
            self._volume_manager.create_volume(vname, labels=labels)

        composition = composition_for_spec(
            SessionSpec(
                agent=config.agent,
                provider=config.provider,
                agent_providers=config.agent_providers,
            )
        )
        network, proxy_ip = self._create_session_proxy(
            config, name, composition, vname, volume_reused
        )
        try:
            self._setup.create_session_container(
                config,
                cname,
                name,
                composition,
                labels,
                network,
                proxy_ip,
            )
        except Exception:
            self._resources.rollback_create(config, name, vname, volume_reused)
            raise

        print(f"Session '{name}' created (stopped).", file=sys.stderr)
        from paude.endpoints import warn_for_uncovered_allowed_endpoints

        warn_for_uncovered_allowed_endpoints(
            config.allowed_endpoints,
            config.allowed_domains,
            session_name=name,
        )
        return Session(
            name=name,
            status="stopped",
            workspace=config.workspace,
            created_at=created_at,
            backend_type=self.backend_type,
            container_id=cname,
            volume_name=vname,
            agent=config.agent,
            agent_providers=config.agent_providers,
            credential_providers=config.credential_providers,
        )

    def _create_session_proxy(
        self,
        config: SessionConfig,
        session_name: str,
        composition: AgentComposition,
        vname: str,
        volume_reused: bool,
    ) -> tuple[str | None, str | None]:
        """Set up proxy for session creation, return (network, proxy_ip)."""
        if not config.proxy_image:
            return None, None
        try:
            network, proxy_ip, _creds = self._setup.setup_proxy_for_session(
                self._proxy, config, session_name, composition
            )
        except Exception:
            if not volume_reused:
                self._volume_manager.remove_volume(vname, force=True)
            raise
        return network, proxy_ip

    def start_session_no_attach(self, name: str) -> None:
        """Start containers without attaching (used by create and upgrade)."""
        cname = require_session(self._runner, name)
        if self._runner.container_running(cname):
            return
        # Balance create_session's "created (stopped)" line so it isn't the last
        # status shown for a container that is in fact left running.
        print(f"Starting session '{name}'...", file=sys.stderr)
        agent = self._setup.start_session_containers(name, cname, self._proxy)
        self._setup.start_agent_headless_in_container(cname, agent)

    def start_agent_headless(self, name: str) -> None:
        """Start the agent in headless mode inside the container."""
        cname = require_running_session(self._runner, name)
        agent = get_session_agent(self._runner, name)
        self._setup.start_agent_headless_in_container(cname, agent)

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
            self._resources.cleanup_all(name, vname)
            return

        print(f"Deleting session '{name}'...", file=sys.stderr)
        self._port_forward.stop(name)
        if self._runner.container_running(cname):
            print(f"Stopping container {cname}...", file=sys.stderr)
            self._runner.stop_container_graceful(cname)
        pname = proxy_container_name(name)
        if self._runner.container_exists(pname):
            print(f"Removing proxy {pname}...", file=sys.stderr)
            self._runner.stop_container(pname)
            self._runner.remove_container_verified(pname)
        print(f"Removing container {cname}...", file=sys.stderr)
        self._runner.remove_container_verified(cname)
        self._resources.cleanup_all(name, vname)

    def start_session(
        self,
        name: str,
        forward_ports: list[ForwardPort] | None = None,
    ) -> int:
        """Start a session and connect to it."""
        cname = require_session(self._runner, name)

        state = self._runner.get_container_state(cname)

        if state == "running":
            print(
                f"Session '{name}' is already running, connecting...",
                file=sys.stderr,
            )
            return self.connect_session(name, forward_ports)

        print(f"Starting session '{name}'...", file=sys.stderr)

        agent = self._setup.start_session_containers(name, cname, self._proxy)
        composition = get_session_composition(self._runner, name)

        return self._attach_with_port_forward(
            name, cname, agent, composition, forward_ports
        )

    def _attach_with_port_forward(
        self,
        name: str,
        cname: str,
        agent: Agent,
        composition: AgentComposition | None = None,
        forward_ports: list[ForwardPort] | None = None,
    ) -> int:
        """Start port forwarding, attach to container, and clean up on exit."""
        runtime = composition or agent
        ports = self._collect_forward_ports(runtime, forward_ports)
        if ports:
            self._port_forward.start(name, cname, ports)
        self._setup.print_port_urls(name, runtime)
        try:
            exit_code = self._runner.attach_container(
                cname,
                entrypoint=CONTAINER_ENTRYPOINT,
                extra_env=self._setup.build_attach_env(runtime),
            )
        finally:
            self._port_forward.stop(name)
        self._setup.print_port_urls(name, runtime)
        return exit_code

    def _collect_forward_ports(
        self,
        agent: Agent | AgentComposition,
        forward_ports: list[ForwardPort] | None,
    ) -> list[ForwardPort]:
        """Merge agent-declared ports with user opt-in forwards for this attach."""
        user_ports = forward_ports or []
        declared = (
            agent.exposed_ports
            if isinstance(agent, AgentComposition)
            else agent.config.exposed_ports
        )
        return merge_forward_ports(user_ports, declared)

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

        self._port_forward.stop(name)
        self._proxy.stop_if_needed(name)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(
        self,
        name: str,
        forward_ports: list[ForwardPort] | None = None,
    ) -> int:
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
        composition = get_session_composition(self._runner, name)
        agent = composition.primary
        from paude.backends.podman.helpers import get_session_credential_providers

        proxy_creds = self._setup.gather_proxy_credentials(
            composition, get_session_credential_providers(self._runner, name)
        )
        self._proxy.start_if_needed(name, credentials=proxy_creds)
        self._proxy.distribute_ca_cert(name)

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

        # Re-sync config on every connect (refreshes if user updated config)
        self._setup.sync_host_config(cname, agent.config.name)
        self._setup.sync_sandbox_config(cname, name)

        print(f"Connecting to session '{name}'...", file=sys.stderr)
        composition = get_session_composition(self._runner, name)
        return self._attach_with_port_forward(
            name, cname, agent, composition, forward_ports
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
        require_session(self._runner, name)
        return self._proxy.get_allowed_domains(name)

    def get_allowed_endpoints(self, name: str) -> list[str] | None:
        """Get current allowed endpoints for a session."""
        require_session(self._runner, name)
        return self._proxy.get_allowed_endpoints(name)

    def get_proxy_blocked_log(self, name: str) -> str | None:
        """Get raw blocked-domain log from the proxy container."""
        require_session(self._runner, name)
        return self._proxy.get_blocked_log(name)

    def update_allowed_domains(
        self,
        name: str,
        domains: list[str],
        *,
        refresh_credentials: bool = False,
    ) -> None:
        """Update allowed domains for a session."""
        require_session(self._runner, name)
        composition = get_session_composition(self._runner, name)
        from paude.backends.podman.helpers import get_session_credential_providers
        from paude.backends.proxy_config import (
            ProxyCredentials,
            proxy_credential_targets,
            required_proxy_credential_targets,
        )

        providers = get_session_credential_providers(self._runner, name)
        refresh = (
            self._setup.gather_proxy_credentials(composition, providers)
            if refresh_credentials
            else ProxyCredentials(chatgpt_oauth_mode="chatgpt" in providers)
        )
        self._proxy.update_domains(
            name,
            domains,
            credentials=refresh,
            credential_targets=proxy_credential_targets(composition),
            required_credentials=required_proxy_credential_targets(
                composition, providers
            ),
        )

    def update_allowed_endpoints(self, name: str, endpoints: list[str]) -> None:
        """Update destination-scoped port exceptions for a session."""
        from paude.endpoints import normalize_allowed_endpoints

        endpoints = normalize_allowed_endpoints(endpoints)
        require_session(self._runner, name)
        composition = get_session_composition(self._runner, name)
        from paude.backends.podman.helpers import get_session_credential_providers
        from paude.backends.proxy_config import (
            ProxyCredentials,
            proxy_credential_targets,
            required_proxy_credential_targets,
        )

        providers = get_session_credential_providers(self._runner, name)
        self._proxy.update_endpoints(
            name,
            endpoints,
            credentials=ProxyCredentials(chatgpt_oauth_mode="chatgpt" in providers),
            credential_targets=proxy_credential_targets(composition),
            required_credentials=required_proxy_credential_targets(
                composition, providers
            ),
        )

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        """Execute a command inside a running session's container."""
        cname = require_running_session(self._runner, name)

        result = self._runner.exec_in_container(
            cname, ["bash", "-c", command], check=False
        )
        return (result.returncode, result.stdout, result.stderr)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        """Copy a file or directory from local to a running session."""
        cname = require_running_session(self._runner, name)
        copy_to_container(self._engine, cname, local_path, remote_path)

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        """Copy a file or directory from a running session to local."""
        cname = require_running_session(self._runner, name)
        copy_from_container(self._engine, cname, remote_path, local_path)

    def stop_container(self, name: str) -> None:
        """Stop a container by name."""
        self._runner.stop_container(name)
