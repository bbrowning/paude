"""Proxy container lifecycle methods extracted from ContainerRunner."""

from __future__ import annotations

import time
from collections.abc import Mapping

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
        otel_ports: list[int] | None = None,
        credentials: Mapping[str, str] | None = None,
        allowed_clients: str | None = None,
        credential_env: Mapping[str, str] | None = None,
        secret_refs: list[str] | None = None,
    ) -> list[str]:
        """Build environment variable arguments for proxy containers."""
        args: list[str] = []
        if dns:
            args.extend(["-e", f"PROXY_DNS={dns}"])
        if allowed_domains:
            args.extend(["-e", f"ALLOWED_DOMAINS={','.join(allowed_domains)}"])
        if otel_ports:
            args.extend(
                ["-e", f"ALLOWED_OTEL_PORTS={','.join(str(p) for p in otel_ports)}"]
            )
        secret_targets = {
            part.split("=", 1)[1]
            for ref in (secret_refs or [])
            for part in ref.split(",")
            if part.startswith("target=")
        }
        if credentials:
            for key, value in credentials.items():
                if key not in secret_targets:
                    args.extend(["-e", f"{key}={value}"])
        if credential_env:
            for key, value in credential_env.items():
                args.extend(["-e", f"{key}={value}"])
        if allowed_clients:
            args.extend(["-e", f"PAUDE_PROXY_ALLOWED_CLIENTS={allowed_clients}"])
        return args

    @staticmethod
    def _build_secret_args(secret_refs: list[str] | None = None) -> list[str]:
        """Build ``--secret`` arguments for podman create."""
        args: list[str] = []
        if secret_refs:
            for ref in secret_refs:
                args.extend(["--secret", ref])
        return args

    def _build_volume_args(
        self,
        ca_volume: str | None = None,
        auth_volume: str | None = None,
    ) -> list[str]:
        """Build volume mount arguments for proxy containers."""
        args: list[str] = []
        if ca_volume:
            args.extend(["-v", f"{ca_volume}:/data/ca"])
        if auth_volume:
            args.extend(["-v", f"{auth_volume}:/data/auth"])
        return args

    def create_session_proxy(
        self,
        name: str,
        image: str,
        network: str,
        dns: str | None = None,
        allowed_domains: list[str] | None = None,
        ip: str | None = None,
        otel_ports: list[int] | None = None,
        ca_volume: str | None = None,
        credentials: Mapping[str, str] | None = None,
        allowed_clients: str | None = None,
        secret_refs: list[str] | None = None,
        credential_env: Mapping[str, str] | None = None,
        auth_volume: str | None = None,
    ) -> str:
        """Create a proxy container for a session (does not start it).

        When *secret_refs* is provided, credentials are injected via
        ``--secret`` flags (podman only) instead of ``-e`` environment
        variables, so they do not appear in ``podman inspect`` output.

        Returns:
            Container name.
        """
        net_args = self._build_multi_network(network, ip=ip)
        env_args = self._build_env_args(
            dns,
            allowed_domains,
            otel_ports,
            credentials,
            allowed_clients,
            credential_env,
            secret_refs,
        )
        secret_args = self._build_secret_args(secret_refs)
        vol_args = self._build_volume_args(ca_volume, auth_volume)

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
            *secret_args,
            *vol_args,
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
        otel_ports: list[int] | None = None,
        ca_volume: str | None = None,
        credentials: Mapping[str, str] | None = None,
        allowed_clients: str | None = None,
        secret_refs: list[str] | None = None,
        credential_env: Mapping[str, str] | None = None,
        auth_volume: str | None = None,
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
            otel_ports=otel_ports,
            ca_volume=ca_volume,
            credentials=credentials,
            allowed_clients=allowed_clients,
            secret_refs=secret_refs,
            credential_env=credential_env,
            auth_volume=auth_volume,
        )
        self.start_session_proxy(name)

        return name
