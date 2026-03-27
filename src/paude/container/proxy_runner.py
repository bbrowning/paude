"""Proxy container lifecycle methods extracted from ContainerRunner."""

from __future__ import annotations

import time

from paude.container.engine import ContainerEngine
from paude.container.runner import ContainerRunner


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


class ProxyRunner:
    """Proxy container lifecycle operations.

    Wraps a ContainerRunner to provide proxy-specific create/start/stop
    operations. Handles engine differences (e.g. Docker multi-network).
    """

    _proxy_counter = 0

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    @property
    def _engine(self) -> ContainerEngine:
        return self._runner.engine

    def _build_multi_network(self, internal: str, ip: str | None = None) -> list[str]:
        """Build network arguments for proxy containers.

        Podman supports ``--network net1,net2`` in create/run.
        Docker requires creating with one network, then connecting
        the second.

        When *ip* is given, separate ``--network`` flags are used because
        per-network options (``net:ip=…``) and comma-separated network
        lists are incompatible in Podman.
        """
        bridge = self._engine.default_bridge_network
        if self._engine.supports_multi_network_create:
            if ip:
                return ["--network", f"{internal}:ip={ip}", "--network", bridge]
            return ["--network", f"{internal},{bridge}"]
        return ["--network", internal]

    def _connect_bridge_if_needed(self, container_name: str) -> None:
        """Connect the container to the default bridge (Docker only)."""
        if self._engine.supports_multi_network_create:
            return
        bridge = self._engine.default_bridge_network
        self._engine.run("network", "connect", bridge, container_name, check=False)

    def _build_env_args(
        self,
        dns: str | None,
        allowed_domains: list[str] | None,
    ) -> list[str]:
        """Build environment variable arguments for proxy containers."""
        args: list[str] = []
        if dns:
            args.extend(["-e", f"SQUID_DNS={dns}"])
        if allowed_domains:
            from paude.domains import format_domains_as_squid_acls

            args.extend(["-e", f"ALLOWED_DOMAINS={','.join(allowed_domains)}"])
            acls = format_domains_as_squid_acls(allowed_domains)
            args.extend(["-e", f"ALLOWED_DOMAIN_ACLS={acls}"])
        return args

    def run_proxy(
        self,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
    ) -> str:
        """Start a proxy container (auto-remove on stop).

        Returns:
            Container name.

        Raises:
            ProxyStartError: If the proxy container fails to start.
        """
        ProxyRunner._proxy_counter += 1
        session_id = f"{int(time.time())}-{ProxyRunner._proxy_counter}"
        container_name = f"paude-proxy-{session_id}"

        net_args = self._build_multi_network(network)
        env_args = self._build_env_args(dns, allowed_domains)

        result = self._engine.run(
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            *net_args,
            *env_args,
            image,
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStartError(f"Failed to start proxy: {result.stderr}")

        self._connect_bridge_if_needed(container_name)
        time.sleep(1)

        return container_name

    def create_session_proxy(
        self,
        name: str,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
        ip: str | None = None,
    ) -> str:
        """Create a proxy container for a session (does not start it).

        Returns:
            Container name.
        """
        net_args = self._build_multi_network(network, ip=ip)
        env_args = self._build_env_args(dns, allowed_domains)

        ip_args: list[str] = []
        if ip and not self._engine.supports_multi_network_create:
            # Docker doesn't support multi-network create, so --ip is separate
            ip_args = ["--ip", ip]

        result = self._engine.run(
            "create",
            "--pull=never",
            "--name",
            name,
            *net_args,
            *ip_args,
            *env_args,
            image,
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStartError(f"Failed to create proxy: {result.stderr}")

        self._connect_bridge_if_needed(name)
        return name

    def start_session_proxy(self, name: str) -> None:
        """Start a session proxy container and wait for initialization.

        Raises:
            ProxyStartError: If the proxy fails to start.
        """
        result = self._engine.run("start", name, check=False)
        if result.returncode != 0:
            raise ProxyStartError(f"Failed to start proxy: {result.stderr}")
        time.sleep(1)

    def recreate_session_proxy(
        self,
        name: str,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
        ip: str | None = None,
    ) -> str:
        """Recreate a session proxy with new configuration.

        Returns:
            Container name.
        """
        self._runner.stop_container(name)
        self._runner.remove_container(name, force=True)

        self.create_session_proxy(
            name=name,
            image=image,
            network=network,
            dns=dns,
            allowed_domains=allowed_domains,
            ip=ip,
        )
        self.start_session_proxy(name)

        return name
