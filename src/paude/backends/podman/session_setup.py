"""Session preparation: credential injection, config sync, and port URLs."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from paude.agents.base import AgentComposition
from paude.agents.codex_config import (
    CODEX_AUTH_TARGET,
    CODEX_CONFIG_TARGET,
    LEGACY_CODEX_PROFILE_TARGET,
    reconcile_codex_config,
)
from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_AGENT_PROVIDERS,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_ENDPOINTS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_OTEL_PORTS,
    PAUDE_LABEL_PROVIDER,
    PAUDE_LABEL_PROVIDERS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
    encode_agent_providers,
    encode_providers,
)
from paude.backends.podman.helpers import (
    container_name,
    get_session_composition,
    get_session_credential_providers,
    get_session_labels,
    proxy_container_name,
    volume_name,
)
from paude.backends.proxy_config import (
    ProxyCredentials,
    derive_agent_ip,
)
from paude.backends.session_env import (
    build_session_env,
    encode_path,
    generate_sandbox_config_script,
)
from paude.constants import (
    CONTAINER_ENTRYPOINT,
    CONTAINER_WORKSPACE,
    GCP_ADC_TARGET,
    SANDBOX_CONFIG_TARGET,
)
from paude.container.engine import ContainerEngine
from paude.container.files import ContainerFileManager
from paude.container.runner import ContainerRunner

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentComposition
    from paude.backends.base import SessionConfig
    from paude.backends.podman.proxy import PodmanProxyManager


class SessionSetup:
    """Handles credential injection, config sync, and port URLs for sessions."""

    def __init__(self, runner: ContainerRunner, engine: ContainerEngine) -> None:
        self._runner = runner
        self._engine = engine
        self._files = ContainerFileManager(engine)

    def get_port_urls(self, agent: Agent | AgentComposition) -> list[str]:
        """Get port-forward URL strings for an agent."""
        ports = (
            agent.exposed_ports
            if isinstance(agent, AgentComposition)
            else agent.config.exposed_ports
        )
        return [f"http://localhost:{hp}" for hp, _cp in ports]

    def read_openclaw_token(self, cname: str) -> str | None:
        """Read the OpenClaw auth token from the container's config file."""
        from paude.backends.proxy_config import OPENCLAW_AUTH_READER_SCRIPT

        result = self._runner.exec_in_container(
            cname, ["python3", "-c", OPENCLAW_AUTH_READER_SCRIPT], check=False
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            return token if token else None
        return None

    def print_port_urls(
        self, session_name: str, agent: Agent | AgentComposition
    ) -> None:
        """Print access URLs for any exposed ports."""
        from paude.backends.session_env import enrich_port_url

        agents = agent.agents if isinstance(agent, AgentComposition) else [agent]
        token = None
        for item in agents:
            if item.config.name == "openclaw":
                token = self.read_openclaw_token(container_name(session_name))
                break
        ports = (
            agent.exposed_ports
            if isinstance(agent, AgentComposition)
            else agent.config.exposed_ports
        )
        for host_port, _container_port in ports:
            url = enrich_port_url(f"http://localhost:{host_port}", token)
            print(
                f"{agents[0].config.display_name} UI available at: {url}",
                file=sys.stderr,
            )

    def build_attach_env(
        self, agent: Agent | AgentComposition
    ) -> dict[str, str] | None:
        """Build extra environment for container attachment."""
        extra_env: dict[str, str] = {}

        port_urls = self.get_port_urls(agent)
        if port_urls:
            extra_env["PAUDE_PORT_URLS"] = ";".join(port_urls)

        return extra_env or None

    def sync_host_config(self, cname: str, agent_name: str) -> None:
        """Copy host config files into /credentials/ via podman cp."""
        from paude.backends.podman.sync import ConfigSyncer

        ConfigSyncer(self._engine).sync(cname, agent_name)

    def sync_sandbox_config(self, cname: str, session_name: str) -> None:
        """Generate and write agent sandbox config script into container."""
        labels = get_session_labels(self._runner, session_name)
        composition = get_session_composition(self._runner, session_name)
        workspace = (
            self._runner.get_container_env(cname, "PAUDE_WORKSPACE")
            or CONTAINER_WORKSPACE
        )
        args = self._runner.get_container_env(cname, "PAUDE_AGENT_ARGS") or ""
        yolo = labels.get(PAUDE_LABEL_YOLO) == "1"
        content = generate_sandbox_config_script(
            composition, workspace, args, yolo=yolo
        )
        self._runner.inject_file(
            cname,
            content,
            SANDBOX_CONFIG_TARGET,
            owner="paude",
        )

    @staticmethod
    def local_adc_path() -> Path | None:
        """Return the local GCP ADC file path, or None if it doesn't exist."""
        from paude.backends.proxy_config import local_gcp_adc_path

        return local_gcp_adc_path()

    def gather_proxy_credentials(
        self,
        composition: AgentComposition,
        credential_providers: list[str] | None = None,
    ) -> ProxyCredentials:
        """Gather real credentials from host environment for the proxy container."""
        from paude.backends.proxy_config import gather_proxy_credentials

        return gather_proxy_credentials(
            composition,
            credential_providers=credential_providers,
            gcp_adc_path=self.local_adc_path(),
        )

    def inject_stub_credentials(self, cname: str) -> None:
        """Inject stub GCP ADC into a running container."""
        from paude.backends.proxy_config import STUB_ADC_JSON

        self._runner.inject_file(cname, STUB_ADC_JSON, GCP_ADC_TARGET, owner="paude")

    def configure_codex(self, cname: str, *, chatgpt_mode: bool) -> None:
        """Reconcile Paude's settings with Codex's persistent default config."""
        default_content = self._files.read_file(cname, CODEX_CONFIG_TARGET)
        legacy_content = self._files.read_file(cname, LEGACY_CODEX_PROFILE_TARGET)
        update = reconcile_codex_config(
            default_content,
            legacy_content,
            chatgpt_mode=chatgpt_mode,
        )
        if update.changed:
            self._files.replace_file(
                cname,
                CODEX_CONFIG_TARGET,
                update.content,
                owner="paude",
            )
        if update.remove_legacy_profile:
            self._files.remove_file(cname, LEGACY_CODEX_PROFILE_TARGET)
        if not chatgpt_mode:
            self._runner.exec_in_container(
                cname, ["rm", "-f", CODEX_AUTH_TARGET], check=False
            )

    def fix_volume_permissions(self, container_name: str) -> None:
        """Reconcile /pvc ownership before configure_codex and the entrypoint run.

        Thin lifecycle hook over
        ``ContainerRunner.reconcile_volume_ownership`` (see there for the
        drifted-UID rationale and why the target is resolved at runtime rather
        than assumed). Kept in the start path so a volume whose owner no longer
        matches the possibly-rebuilt image is fixed up before anything reads or
        writes it — on both podman and docker.
        """
        self._runner.reconcile_volume_ownership(container_name)

    def start_session_containers(
        self,
        name: str,
        cname: str,
        proxy: PodmanProxyManager,
    ) -> Agent:
        """Start proxy and agent containers, inject credentials and config.

        Returns:
            The resolved agent.
        """
        composition = get_session_composition(self._runner, name)
        credential_providers = get_session_credential_providers(self._runner, name)
        agent = composition.primary
        proxy_creds = self.gather_proxy_credentials(composition, credential_providers)
        proxy.start_if_needed(name, credentials=proxy_creds)
        self._runner.start_container(cname)
        self.fix_volume_permissions(cname)
        proxy.distribute_ca_cert(name)
        self.inject_stub_credentials(cname)
        codex_agents = [
            item for item in composition.agents if item.config.name == "codex"
        ]
        if codex_agents:
            self.configure_codex(
                cname,
                chatgpt_mode=any(
                    item.config.provider == "chatgpt" for item in codex_agents
                ),
            )
        self.sync_host_config(cname, agent.config.name)
        self.sync_sandbox_config(cname, name)
        return agent

    def start_agent_headless_in_container(
        self,
        cname: str,
        agent: Agent,
    ) -> None:
        """Start the agent in headless mode inside a running container."""
        env_vars = self.build_attach_env(agent)
        cmd: list[str] = ["env", "PAUDE_HEADLESS=1"]
        if env_vars:
            for key, value in env_vars.items():
                cmd.append(f"{key}={value}")
        cmd.append(CONTAINER_ENTRYPOINT)
        result = self._runner.exec_in_container(cname, cmd, check=False)
        if result.returncode != 0:
            print(
                f"Warning: headless agent start failed "
                f"(exit {result.returncode}). "
                f"Agent will start on next 'paude connect'.",
                file=sys.stderr,
            )

    @staticmethod
    def build_session_labels(
        config: SessionConfig,
        session_name: str,
        created_at: str,
    ) -> dict[str, str]:
        """Build container labels for a new session."""
        from paude import __version__

        labels: dict[str, str] = {
            "app": "paude",
            PAUDE_LABEL_SESSION: session_name,
            PAUDE_LABEL_WORKSPACE: encode_path(config.workspace, url_safe=True),
            PAUDE_LABEL_CREATED: created_at,
            PAUDE_LABEL_AGENT: config.agent,
            PAUDE_LABEL_VERSION: __version__,
        }
        specs = config.agent_providers or [(config.agent, config.provider or "")]
        labels[PAUDE_LABEL_AGENT_PROVIDERS] = encode_agent_providers(specs)
        labels[PAUDE_LABEL_PROVIDERS] = encode_providers(config.credential_providers)
        if config.provider:
            labels[PAUDE_LABEL_PROVIDER] = config.provider
        if config.gpu:
            labels[PAUDE_LABEL_GPU] = config.gpu
        if config.yolo:
            labels[PAUDE_LABEL_YOLO] = "1"
        labels[PAUDE_LABEL_DOMAINS] = ",".join(config.allowed_domains)
        labels[PAUDE_LABEL_ENDPOINTS] = ",".join(config.allowed_endpoints)
        if config.proxy_image:
            labels[PAUDE_LABEL_PROXY_IMAGE] = config.proxy_image
        if config.otel_ports:
            labels[PAUDE_LABEL_OTEL_PORTS] = ",".join(str(p) for p in config.otel_ports)
        if config.otel_endpoint:
            labels[PAUDE_LABEL_OTEL_ENDPOINT] = config.otel_endpoint
        return labels

    def setup_proxy_for_session(
        self,
        proxy: PodmanProxyManager,
        config: SessionConfig,
        session_name: str,
        composition: AgentComposition,
    ) -> tuple[str | None, str | None, ProxyCredentials]:
        """Create and start proxy sidecar for the session.

        Returns:
            (network, proxy_ip, proxy_credentials) tuple.
        """
        proxy_creds = self.gather_proxy_credentials(
            composition, config.credential_providers
        )
        network, proxy_ip = proxy.create_proxy(
            session_name,
            config.proxy_image or "",
            config.allowed_domains,
            allowed_endpoints=config.allowed_endpoints,
            otel_ports=config.otel_ports,
            credentials=proxy_creds,
        )
        if proxy_ip is None:
            # Reached only on DNS-enabled networks (Docker); create_proxy()
            # raises on --disable-dns networks (Podman) where the hostname
            # wouldn't resolve. Source-IP filtering and proxy DNS are skipped.
            print(
                "WARNING: Could not determine proxy IP; falling back to the "
                "proxy hostname (source-IP filtering disabled).",
                file=sys.stderr,
            )
        proxy.start_proxy(session_name)
        return network, proxy_ip, proxy_creds

    def create_session_container(
        self,
        config: SessionConfig,
        cname: str,
        session_name: str,
        composition: AgentComposition,
        labels: dict[str, str],
        network: str | None,
        proxy_ip: str | None,
    ) -> None:
        """Build env/mounts and create the stopped session container."""
        mounts = list(config.mounts)
        mounts.extend(["-v", f"{volume_name(session_name)}:/pvc"])
        proxy_name = (
            (proxy_ip or proxy_container_name(session_name))
            if config.proxy_image
            else None
        )
        env, _agent_args = build_session_env(config, composition, proxy_name=proxy_name)
        env["PAUDE_WORKSPACE"] = CONTAINER_WORKSPACE
        dns = [proxy_ip] if proxy_ip else None
        agent_ip = derive_agent_ip(proxy_ip) if proxy_ip else None
        print(f"Creating container {cname}...", file=sys.stderr)
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
            ports=None,
        )
