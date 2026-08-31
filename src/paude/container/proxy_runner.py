"""Proxy container lifecycle methods extracted from ContainerRunner."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from secrets import token_hex

from paude.container.engine import ContainerEngine
from paude.container.proxy_inspect import ProxyInspectionError, ProxyInspector
from paude.container.runner import ContainerRunner


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


@dataclass
class ProxySwap:
    """A started candidate proxy whose retained predecessor can be restored."""

    runner: ContainerRunner
    name: str
    backup_name: str
    network: str
    ip: str | None
    old_running: bool
    old_renamed: bool = False
    old_disconnected: bool = False
    candidate_created: bool = False

    def commit(self) -> None:
        """Remove the retained predecessor after all update state is durable."""
        self._run_checked("remove retained proxy", "rm", "-f", self.backup_name)

    def rollback(self) -> None:
        """Remove the candidate and restore the predecessor's identity/state."""
        failures: list[str] = []
        if self.candidate_created:
            self._try(failures, "remove candidate proxy", "rm", "-f", self.name)
        if self.old_disconnected:
            args = ["network", "connect"]
            if self.ip:
                args.extend(["--ip", self.ip])
            args.extend([self.network, self.backup_name])
            self._try(failures, "reconnect retained proxy", *args)
        if self.old_renamed:
            self._try(
                failures,
                "restore retained proxy name",
                "rename",
                self.backup_name,
                self.name,
            )
        if self.old_running:
            self._try(failures, "restart retained proxy", "start", self.name)
        if failures:
            raise ProxyStartError("; ".join(failures))

    def _try(self, failures: list[str], operation: str, *args: str) -> None:
        try:
            self._run_checked(operation, *args)
        except ProxyStartError as exc:
            failures.append(str(exc))

    def _run_checked(self, operation: str, *args: str) -> None:
        result = self.runner.engine.run(*args, check=False)
        if result.returncode != 0:
            detail = result.stderr.strip() or "container engine command failed"
            raise ProxyStartError(f"Failed to {operation}: {detail}")


class ProxyRunner:
    """Proxy container lifecycle operations.

    Wraps a ContainerRunner to provide proxy-specific create/start/stop
    operations. Handles engine differences (e.g. Docker multi-network).
    """

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner
        self._inspector = ProxyInspector(runner)

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
        result = self._engine.run(
            "network", "connect", bridge, container_name, check=False
        )
        if result.returncode != 0:
            raise ProxyStartError(
                "Failed to connect proxy to the bridge network: "
                f"{result.stderr.strip() or 'container engine command failed'}"
            )

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

        try:
            self._connect_bridge_if_needed(name)
        except Exception:
            self._engine.run("rm", "-f", name, check=False)
            raise
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

    def _require_running_candidate(self, name: str) -> None:
        """Fail when a started replacement did not survive initialization."""
        try:
            running = self._inspector.running(name)
        except ProxyInspectionError as exc:
            raise ProxyStartError(
                f"Failed to verify replacement proxy startup: {exc}"
            ) from exc
        if not running:
            raise ProxyStartError(
                "Replacement proxy exited during initialization; "
                "the previous proxy will be restored."
            )

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

    def swap_session_proxy(
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
    ) -> ProxySwap:
        """Start a replacement while retaining the old proxy for rollback."""
        swap = ProxySwap(
            runner=self._runner,
            name=name,
            backup_name=f"{name}-rollback-{token_hex(4)}",
            network=network,
            ip=ip,
            old_running=self._runner.container_running(name),
        )
        try:
            if swap.old_running:
                swap._run_checked("stop current proxy", "stop", "-t", "1", name)
            swap._run_checked(
                "retain current proxy",
                "rename",
                name,
                swap.backup_name,
            )
            swap.old_renamed = True
            swap._run_checked(
                "release current proxy address",
                "network",
                "disconnect",
                network,
                swap.backup_name,
            )
            swap.old_disconnected = True
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
            swap.candidate_created = True
            self.start_session_proxy(name)
            self._require_running_candidate(name)
        except Exception as primary:
            try:
                swap.rollback()
            except Exception as rollback:
                raise ProxyStartError(
                    f"Proxy replacement failed: {primary}; rollback failed: {rollback}"
                ) from primary
            raise
        return swap
