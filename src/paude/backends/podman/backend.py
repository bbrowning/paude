"""Podman/Docker backend implementation."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from paude.backends.base import Session, SessionConfig

if TYPE_CHECKING:
    from paude.agents.base import Agent
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
from paude.backends.podman.port_forward import PodmanPortForwardManager
from paude.backends.podman.proxy import PodmanProxyManager
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_OTEL_PORTS,
    PAUDE_LABEL_PROVIDER,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
    build_session_env,
    derive_agent_ip,
    encode_path,
    generate_sandbox_config_script,
)
from paude.constants import (
    CONTAINER_ENTRYPOINT,
    CONTAINER_WORKSPACE,
    GCP_ADC_SECRET_NAME,
    GCP_ADC_TARGET,
    SANDBOX_CONFIG_TARGET,
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
        self._port_forward = PodmanPortForwardManager(self._engine)

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

    def _get_session_labels(self, session_name: str) -> dict[str, str]:
        """Look up container labels for a session."""
        container = find_container_by_session_name(self._runner, session_name)
        return (container.get("Labels", {}) or {}) if container else {}

    def _get_session_agent(self, session_name: str) -> Agent:
        """Get the agent instance for a session from its container labels."""
        from paude.agents import get_agent

        labels = self._get_session_labels(session_name)
        agent_name = str(labels.get(PAUDE_LABEL_AGENT, "claude"))
        provider = labels.get(PAUDE_LABEL_PROVIDER) or None
        return get_agent(agent_name, provider=provider)

    def _get_port_urls(self, agent: Agent) -> list[str]:
        """Get port-forward URL strings for an agent."""
        return [f"http://localhost:{hp}" for hp, _cp in agent.config.exposed_ports]

    def _read_openclaw_token(self, cname: str) -> str | None:
        """Read the OpenClaw auth token from the container's config file."""
        from paude.backends.shared import OPENCLAW_AUTH_READER_SCRIPT

        result = self._runner.exec_in_container(
            cname, ["python3", "-c", OPENCLAW_AUTH_READER_SCRIPT], check=False
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            return token if token else None
        return None

    def _print_port_urls(self, session_name: str, agent: Agent) -> None:
        """Print access URLs for any exposed ports."""
        from paude.backends.shared import enrich_port_url

        token = None
        if agent.config.name == "openclaw":
            token = self._read_openclaw_token(container_name(session_name))
        for host_port, _container_port in agent.config.exposed_ports:
            url = enrich_port_url(f"http://localhost:{host_port}", token)
            print(
                f"{agent.config.display_name} UI available at: {url}",
                file=sys.stderr,
            )

    def _start_port_forward(self, session_name: str, agent: Agent) -> None:
        """Start port-forwarding if the agent has exposed ports."""
        ports = agent.config.exposed_ports
        if not ports:
            return
        cname = container_name(session_name)
        self._port_forward.start(session_name, cname, ports)

    def _build_attach_env(self, agent: Agent) -> dict[str, str] | None:
        """Build extra environment for container attachment.

        No credentials are passed here — all authentication is handled
        by the proxy sidecar. Dummy values are already baked into the
        container env via build_session_env().
        """
        extra_env: dict[str, str] = {}

        port_urls = self._get_port_urls(agent)
        if port_urls:
            extra_env["PAUDE_PORT_URLS"] = ";".join(port_urls)

        return extra_env or None

    def _sync_host_config(self, cname: str, agent_name: str) -> None:
        """Copy host config files into /credentials/ via podman cp.

        Delegates to ConfigSyncer which mirrors the OpenShift pattern.
        Skipped for SSH remotes which use bind mounts instead.
        """
        from paude.backends.podman.sync import ConfigSyncer

        ConfigSyncer(self._engine).sync(cname, agent_name)

    def _sync_sandbox_config(self, cname: str, session_name: str) -> None:
        """Generate and write agent sandbox config script into container."""
        labels = self._get_session_labels(session_name)
        agent_name = str(labels.get(PAUDE_LABEL_AGENT, "claude"))
        provider = labels.get(PAUDE_LABEL_PROVIDER) or None
        workspace = (
            self._runner.get_container_env(cname, "PAUDE_WORKSPACE")
            or CONTAINER_WORKSPACE
        )
        args = self._runner.get_container_env(cname, "PAUDE_AGENT_ARGS") or ""
        yolo = labels.get(PAUDE_LABEL_YOLO) == "1"
        content = generate_sandbox_config_script(
            agent_name, workspace, args, provider=provider, yolo=yolo
        )
        self._runner.inject_file(
            cname,
            content,
            SANDBOX_CONFIG_TARGET,
            owner="paude:0",
        )

    @staticmethod
    def _local_adc_path() -> Path | None:
        """Return the local GCP ADC file path, or None if it doesn't exist."""
        from paude.backends.shared import local_gcp_adc_path

        return local_gcp_adc_path()

    def _gather_proxy_credentials(self, agent: Agent) -> dict[str, str]:
        """Gather real credentials from host environment for the proxy container."""
        from paude.backends.shared import gather_proxy_credentials

        return gather_proxy_credentials(
            agent.config, gcp_adc_path=self._local_adc_path()
        )

    def _inject_stub_credentials(self, cname: str) -> None:
        """Inject stub GCP ADC into a running container.

        All real authentication is handled by the proxy sidecar. The agent
        container only gets a stub ADC JSON to satisfy client library checks.
        """
        from paude.backends.shared import STUB_ADC_JSON

        self._runner.inject_file(cname, STUB_ADC_JSON, GCP_ADC_TARGET, owner="paude:0")

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new session (does not start it).

        Raises:
            SessionExistsError: If session with this name already exists.
        """
        session_name = config.name or _generate_session_name(config.workspace)

        cname = container_name(session_name)
        vname = volume_name(session_name)

        if self._runner.container_exists(cname):
            raise SessionExistsError(f"Session '{session_name}' already exists")

        created_at = datetime.now(UTC).isoformat()

        # Create labels
        from paude import __version__

        labels: dict[str, str] = {
            "app": "paude",
            PAUDE_LABEL_SESSION: session_name,
            PAUDE_LABEL_WORKSPACE: encode_path(config.workspace, url_safe=True),
            PAUDE_LABEL_CREATED: created_at,
            PAUDE_LABEL_AGENT: config.agent,
            PAUDE_LABEL_VERSION: __version__,
        }
        if config.provider:
            labels[PAUDE_LABEL_PROVIDER] = config.provider
        if config.gpu:
            labels[PAUDE_LABEL_GPU] = config.gpu
        if config.yolo:
            labels[PAUDE_LABEL_YOLO] = "1"
        labels[PAUDE_LABEL_DOMAINS] = ",".join(config.allowed_domains)
        if config.proxy_image:
            labels[PAUDE_LABEL_PROXY_IMAGE] = config.proxy_image
        if config.otel_ports:
            labels[PAUDE_LABEL_OTEL_PORTS] = ",".join(str(p) for p in config.otel_ports)
        if config.otel_endpoint:
            labels[PAUDE_LABEL_OTEL_ENDPOINT] = config.otel_endpoint

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        # Create volume for workspace persistence (skip if reusing existing)
        if config.reuse_volume and self._volume_manager.volume_exists(vname):
            print(f"Reusing existing volume {vname}...", file=sys.stderr)
        else:
            print(f"Creating volume {vname}...", file=sys.stderr)
            self._volume_manager.create_volume(vname, labels=labels)

        # Resolve agent once for both proxy credential gathering and env building
        from paude.agents import get_agent

        agent = get_agent(config.agent, provider=config.provider)

        network: str | None = None
        proxy_ip: str | None = None
        if config.proxy_image:
            try:
                proxy_creds = self._gather_proxy_credentials(agent)
                network, proxy_ip = self._proxy.create_proxy(
                    session_name,
                    config.proxy_image,
                    config.allowed_domains,
                    otel_ports=config.otel_ports,
                    credentials=proxy_creds,
                )
            except Exception:
                if not config.reuse_volume:
                    self._volume_manager.remove_volume(vname, force=True)
                raise

            if proxy_ip is None:
                print(
                    "WARNING: Could not determine proxy IP; "
                    "container DNS will not use the proxy for resolution.",
                    file=sys.stderr,
                )

            self._proxy.start_proxy(session_name)

        # Derive fixed agent IP so it matches what the proxy expects
        agent_ip = derive_agent_ip(proxy_ip) if proxy_ip else None

        # Build mounts with session volume
        mounts = list(config.mounts)
        mounts.extend(["-v", f"{vname}:/pvc"])

        # Bind-mount host workspace directly for local sessions (avoids
        # the git-remote sync step, which is slow for large repos).
        if not self._engine.is_remote:
            mounts.extend(["-v", f"{config.workspace}:{CONTAINER_WORKSPACE}:z"])

        proxy_name_for_env = (
            (proxy_ip or proxy_container_name(session_name))
            if config.proxy_image
            else None
        )
        env, _agent_args = build_session_env(
            config, agent, proxy_name=proxy_name_for_env
        )
        env["PAUDE_WORKSPACE"] = CONTAINER_WORKSPACE

        # Create container (stopped) — no real credentials are passed.
        # Agent gets stub ADC injected at start time.
        print(f"Creating container {cname}...", file=sys.stderr)
        try:
            dns = [proxy_ip] if proxy_ip else None
            self._runner.create_container(
                name=cname,
                image=config.image,
                mounts=mounts,
                env=env,
                workdir="/pvc",
                labels=labels,
                entrypoint="tini",
                command=["--", "sleep", "infinity"],
                secrets=None,
                network=network,
                network_ip=agent_ip,
                gpu=config.gpu,
                dns=dns,
                ports=None,  # Port-forward proxy handles port access
            )
        except Exception:
            # Cleanup all resources on failure
            if config.proxy_image:
                pname = proxy_container_name(session_name)
                self._runner.remove_container(pname, force=True)
                self._network_manager.remove_network(network_name(session_name))
            self._volume_manager.remove_volume(vname, force=True)
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

    def _start_session_containers(self, name: str, cname: str) -> Agent:
        """Start proxy and agent containers, inject stub credentials and config.

        Shared startup sequence used by both interactive and headless paths.
        No real credentials are injected into the agent container.

        Returns:
            The resolved agent.
        """
        agent = self._get_session_agent(name)
        proxy_creds = self._gather_proxy_credentials(agent)
        self._proxy.start_if_needed(name, credentials=proxy_creds)
        self._runner.start_container(cname)
        self._fix_volume_permissions(cname)
        self._proxy.distribute_ca_cert(name)
        self._inject_stub_credentials(cname)
        self._sync_host_config(cname, agent.config.name)
        self._sync_sandbox_config(cname, name)
        return agent

    def start_session_no_attach(self, name: str) -> None:
        """Start containers without attaching (for git setup, etc.)."""
        cname = self._require_session(name)
        if self._runner.container_running(cname):
            return
        agent = self._start_session_containers(name, cname)
        self._start_agent_headless_in_container(cname, agent)

    def start_agent_headless(self, name: str) -> None:
        """Start the agent in headless mode inside the container."""
        cname = self._require_running_session(name)
        agent = self._get_session_agent(name)
        self._start_agent_headless_in_container(cname, agent)

    def _start_agent_headless_in_container(
        self,
        cname: str,
        agent: Agent,
    ) -> None:
        """Start the agent in headless mode (internal, skips session lookup)."""
        env_vars = self._build_attach_env(agent)
        cmd: list[str] = ["env", "PAUDE_HEADLESS=1"]
        if env_vars:
            for key, value in env_vars.items():
                cmd.append(f"{key}={value}")
        cmd.append(CONTAINER_ENTRYPOINT)
        result = self._runner.exec_in_container(cname, cmd, check=False)
        if result.returncode != 0:
            print(
                f"Warning: headless agent start failed (exit {result.returncode}). "
                f"Agent will start on next 'paude connect'.",
                file=sys.stderr,
            )

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

        self._port_forward.stop(name)

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

        # Remove CA volume if it exists
        from paude.backends.podman.proxy import ca_volume_name

        ca_vol = ca_volume_name(name)
        if self._volume_manager.volume_exists(ca_vol):
            self._volume_manager.remove_volume(ca_vol, force=True)

        # Remove proxy credential secrets
        self._proxy.remove_credential_secrets(name)

        # Remove volume and legacy secret
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
            return self.connect_session(name)

        print(f"Starting session '{name}'...", file=sys.stderr)

        agent = self._start_session_containers(name, cname)

        self._start_port_forward(name, agent)
        self._print_port_urls(name, agent)
        exit_code = self._runner.attach_container(
            cname,
            entrypoint=CONTAINER_ENTRYPOINT,
            extra_env=self._build_attach_env(agent),
        )
        self._print_port_urls(name, agent)
        return exit_code

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
        agent = self._get_session_agent(name)
        proxy_creds = self._gather_proxy_credentials(agent)
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
        self._sync_host_config(cname, agent.config.name)
        self._sync_sandbox_config(cname, name)

        self._start_port_forward(name, agent)
        print(f"Connecting to session '{name}'...", file=sys.stderr)
        self._print_port_urls(name, agent)
        try:
            exit_code = self._runner.attach_container(
                cname,
                entrypoint=CONTAINER_ENTRYPOINT,
                extra_env=self._build_attach_env(agent),
            )
        finally:
            self._port_forward.stop(name)
        self._print_port_urls(name, agent)
        return exit_code

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
        """Get raw blocked-domain log from the proxy container."""
        self._require_session(name)
        return self._proxy.get_blocked_log(name)

    def update_allowed_domains(self, name: str, domains: list[str]) -> None:
        """Update allowed domains for a session."""
        self._require_session(name)
        agent = self._get_session_agent(name)
        proxy_creds = self._gather_proxy_credentials(agent)
        self._proxy.update_domains(name, domains, credentials=proxy_creds)

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
