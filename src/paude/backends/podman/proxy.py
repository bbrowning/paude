"""Proxy management for the Podman backend."""

from __future__ import annotations

import sys
import time

from paude.backends.podman.helpers import (
    find_container_by_session_name,
    network_name,
    proxy_container_name,
)
from paude.backends.shared import (
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_OTEL_PORTS,
    PAUDE_LABEL_PROXY_IMAGE,
    SQUID_BLOCKED_LOG_PATH,
)
from paude.container.engine import ContainerEngine
from paude.container.network import NetworkManager
from paude.container.proxy_runner import ProxyRunner
from paude.container.runner import ContainerRunner
from paude.platform import get_podman_machine_dns, is_macos


def _get_host_dns(engine: ContainerEngine) -> str | None:
    """Get the primary DNS server for the container host.

    Reads /etc/resolv.conf on the container host via the engine's
    transport (local or SSH). The only exception is local Podman on
    macOS, where containers run inside a VM — in that case we read
    DNS from the Podman VM instead.
    """
    # Local Podman on macOS: containers run in a VM, so the host's
    # resolv.conf isn't what containers see.
    if engine.binary == "podman" and not engine.is_remote and is_macos():
        dns = get_podman_machine_dns()
        if dns:
            print(f"Using Podman VM DNS: {dns}", file=sys.stderr)
        return dns

    # All other cases: read resolv.conf from the container host
    # (locally or via SSH transport for remote hosts).
    return _read_resolv_conf(engine)


def _read_resolv_conf(engine: ContainerEngine) -> str | None:
    """Read the first non-loopback nameserver from the host's resolv.conf."""
    try:
        result = engine.transport.run(
            ["grep", "nameserver", "/etc/resolv.conf"],
            check=False,
        )
        output = result.stdout.strip()
        if result.returncode == 0 and output:
            for line in output.split("\n"):
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    ip = parts[1]
                    # Skip loopback DNS (e.g. systemd-resolved 127.0.0.53)
                    # — not reachable from inside containers
                    if ip.startswith("127."):
                        continue
                    print(f"Using host DNS: {ip}", file=sys.stderr)
                    return ip
    except Exception:  # noqa: S110 - best-effort DNS discovery
        pass
    return None


CA_CERT_CONTAINER_PATH = "/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"
CA_CERT_POLL_INTERVAL = 1
CA_CERT_POLL_TIMEOUT = 30


def ca_volume_name(session_name: str) -> str:
    """Get the CA certificate volume name for a session."""
    return f"paude-ca-{session_name}"


class PodmanProxyManager:
    """Manages proxy containers for Podman sessions."""

    def __init__(
        self,
        runner: ContainerRunner,
        network_manager: NetworkManager,
    ) -> None:
        self._runner = runner
        self._network_manager = network_manager
        self._proxy_runner = ProxyRunner(runner)

    def has_proxy(self, session_name: str) -> bool:
        """Check if a session has a proxy container."""
        return self._runner.container_exists(proxy_container_name(session_name))

    def get_config_from_labels(
        self, session_name: str
    ) -> tuple[str, list[str], list[int]] | None:
        """Read proxy configuration from the main container's labels.

        Returns:
            Tuple of (proxy_image, domains, otel_ports) or None.
        """
        container = find_container_by_session_name(self._runner, session_name)
        if container is None:
            return None

        labels = container.get("Labels", {}) or {}

        domains_str = labels.get(PAUDE_LABEL_DOMAINS)
        if domains_str is None:
            return None

        proxy_image = labels.get(PAUDE_LABEL_PROXY_IMAGE, "")
        if not proxy_image:
            return None

        domains = [d for d in domains_str.split(",") if d]

        otel_ports_str = labels.get(PAUDE_LABEL_OTEL_PORTS, "")
        otel_ports = [int(p) for p in otel_ports_str.split(",") if p]

        return (proxy_image, domains, otel_ports)

    def start_if_needed(self, session_name: str) -> None:
        """Start or recreate the proxy container for a session."""
        pname = proxy_container_name(session_name)

        if self._runner.container_exists(pname):
            if self._runner.container_running(pname):
                return
            print(f"Starting proxy {pname}...", file=sys.stderr)
            self._proxy_runner.start_session_proxy(pname)
            return

        # Proxy doesn't exist — check if it was expected
        proxy_config = self.get_config_from_labels(session_name)
        if proxy_config is None:
            return

        # Recreate the missing proxy
        proxy_image, domains, otel_ports = proxy_config
        nname = network_name(session_name)
        ca_vol = ca_volume_name(session_name)

        self._network_manager.create_internal_network(
            nname, disable_dns=self._runner.engine.is_podman
        )

        proxy_ip = self._get_proxy_ip(nname)
        dns = _get_host_dns(self._runner.engine)
        print(f"Recreating missing proxy {pname}...", file=sys.stderr)
        self._proxy_runner.create_session_proxy(
            name=pname,
            image=proxy_image,
            network=nname,
            dns=dns,
            allowed_domains=domains,
            ip=proxy_ip,
            otel_ports=otel_ports,
            ca_volume=ca_vol,
        )
        self._proxy_runner.start_session_proxy(pname)

    def start_proxy(self, session_name: str) -> None:
        """Start the proxy container for a session."""
        pname = proxy_container_name(session_name)
        self._proxy_runner.start_session_proxy(pname)

    def distribute_ca_cert(self, session_name: str) -> None:
        """Copy the proxy's CA certificate into the agent container.

        Waits for the proxy to generate its CA cert at /data/ca/ca.crt,
        then copies it into the agent container's trust store and runs
        update-ca-trust.
        """
        from paude.backends.podman.helpers import container_name

        pname = proxy_container_name(session_name)
        cname = container_name(session_name)

        if not self._runner.container_running(pname):
            return
        if not self._runner.container_running(cname):
            return

        # Poll for CA cert generation in proxy container
        elapsed = 0
        while elapsed < CA_CERT_POLL_TIMEOUT:
            result = self._runner.exec_in_container(
                pname, ["test", "-f", "/data/ca/ca.crt"], check=False
            )
            if result.returncode == 0:
                break
            time.sleep(CA_CERT_POLL_INTERVAL)
            elapsed += CA_CERT_POLL_INTERVAL
        else:
            print(
                "WARNING: Timed out waiting for proxy CA certificate.",
                file=sys.stderr,
            )
            return

        # Read CA cert from proxy container
        result = self._runner.exec_in_container(
            pname, ["cat", "/data/ca/ca.crt"], check=False
        )
        if result.returncode != 0 or not result.stdout.strip():
            print(
                "WARNING: Failed to read CA certificate from proxy.",
                file=sys.stderr,
            )
            return

        # Inject into agent container and update trust store
        self._runner.inject_file(
            cname,
            result.stdout,
            CA_CERT_CONTAINER_PATH,
            owner="root:0",
        )
        update_result = self._runner.exec_in_container(
            cname, ["update-ca-trust"], check=False
        )
        if update_result.returncode != 0:
            print(
                "WARNING: update-ca-trust failed in agent container.",
                file=sys.stderr,
            )

    def stop_if_needed(self, session_name: str) -> None:
        """Stop the proxy container for a session if one exists."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return

        if not self._runner.container_running(pname):
            return

        self._runner.stop_container(pname)

    def _get_proxy_ip(self, nname: str) -> str | None:
        """Derive a fixed proxy IP from the network's gateway address.

        On networks with a gateway (normal Podman/Docker networks), the
        proxy IP is gateway + 1 (e.g. 10.89.0.1 → 10.89.0.2).

        On --disable-dns internal networks (no gateway),
        get_network_gateway() derives a synthetic gateway from the
        subnet's first host IP (e.g. 10.89.2.0/24 → 10.89.2.1), and
        derive_proxy_ip() adds 1 → 10.89.2.2. The proxy is then
        explicitly assigned this IP via --network net:ip=10.89.2.2,
        so container creation order does not matter.
        """
        gateway = self._network_manager.get_network_gateway(nname)
        if not gateway:
            return None
        return NetworkManager.derive_proxy_ip(gateway)

    def create_proxy(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None,
        otel_ports: list[int] | None = None,
    ) -> tuple[str, str | None]:
        """Create a proxy container for a session.

        Returns:
            Tuple of (network_name, proxy_ip). proxy_ip is None if the
            network gateway could not be determined.
        """
        if not proxy_image:
            raise ValueError("proxy_image is required when allowed_domains is set")

        nname = network_name(session_name)
        self._network_manager.create_internal_network(
            nname, disable_dns=self._runner.engine.is_podman
        )

        proxy_ip = self._get_proxy_ip(nname)

        # Create a named volume for the CA certificate
        from paude.container.volume import VolumeManager

        ca_vol = ca_volume_name(session_name)
        volume_mgr = VolumeManager(self._runner.engine)
        volume_mgr.create_volume(ca_vol)

        pname = proxy_container_name(session_name)
        dns = _get_host_dns(self._runner.engine)
        print(f"Creating proxy {pname}...", file=sys.stderr)
        try:
            self._proxy_runner.create_session_proxy(
                name=pname,
                image=proxy_image,
                network=nname,
                dns=dns,
                allowed_domains=allowed_domains,
                ip=proxy_ip,
                otel_ports=otel_ports,
                ca_volume=ca_vol,
            )
        except Exception:
            volume_mgr.remove_volume(ca_vol, force=True)
            self._network_manager.remove_network(nname)
            raise

        return nname, proxy_ip

    def get_allowed_domains(self, session_name: str) -> list[str] | None:
        """Get current allowed domains for a session."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return None

        domains_str = self._runner.get_container_env(pname, "ALLOWED_DOMAINS")
        if not domains_str:
            return []

        return [d for d in domains_str.split(",") if d]

    def get_blocked_log(self, session_name: str) -> str | None:
        """Get raw squid blocked log from the proxy container."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return None

        if not self._runner.container_running(pname):
            raise ValueError(f"Proxy for session '{session_name}' is not running.")

        result = self._runner.exec_in_container(
            pname, ["cat", SQUID_BLOCKED_LOG_PATH], check=False
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def update_domains(self, session_name: str, domains: list[str]) -> None:
        """Update allowed domains for a session."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            raise ValueError(
                f"Session '{session_name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        proxy_image = self._runner.get_container_image(pname)
        if not proxy_image:
            raise ValueError(f"Cannot inspect proxy container: {pname}")

        # Preserve OTEL ports from labels across proxy recreate
        proxy_config = self.get_config_from_labels(session_name)
        _, _, otel_ports = proxy_config if proxy_config else ("", [], [])

        nname = network_name(session_name)
        ca_vol = ca_volume_name(session_name)
        proxy_ip = self._get_proxy_ip(nname)
        dns = _get_host_dns(self._runner.engine)

        print(
            f"Updating proxy domains for session '{session_name}'...",
            file=sys.stderr,
        )
        self._proxy_runner.recreate_session_proxy(
            name=pname,
            image=proxy_image,
            network=nname,
            dns=dns,
            allowed_domains=domains,
            ip=proxy_ip,
            otel_ports=otel_ports,
            ca_volume=ca_vol,
        )
