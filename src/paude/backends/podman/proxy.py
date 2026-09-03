"""Proxy management for the Podman backend."""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping

from paude.backends.labels import (
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_ENDPOINTS,
    PAUDE_LABEL_OTEL_PORTS,
    PAUDE_LABEL_PROXY_IMAGE,
)
from paude.backends.podman.ca_cert import _BUILD_CA_BUNDLE_CMD as _BUILD_CA_BUNDLE_CMD
from paude.backends.podman.ca_cert import CACertDistributor
from paude.backends.podman.helpers import auth_volume_name as auth_volume_name
from paude.backends.podman.helpers import ca_volume_name as ca_volume_name
from paude.backends.podman.helpers import (
    find_container_by_session_name,
    network_name,
    proxy_container_name,
)
from paude.backends.podman.proxy_credentials import ProxyCredentialManager
from paude.backends.podman.proxy_state import ProxyStateStore
from paude.backends.proxy_config import CA_CERT_CONTAINER_PATH as CA_CERT_CONTAINER_PATH
from paude.backends.proxy_config import (
    PROXY_BLOCKED_LOG_PATH,
    ProxyCredentials,
    derive_agent_ip,
)
from paude.container.engine import ContainerEngine
from paude.container.network import NetworkManager
from paude.container.proxy_runner import ProxyRunner, ProxyStartError
from paude.container.runner import ContainerRunner
from paude.platform import get_podman_machine_dns, is_macos

# Bounded poll for deriving the proxy IP from a freshly-created network;
# see _get_proxy_ip for the inspect-vs-create race this compensates for.
# The happy path returns on the first attempt with no added latency.
_PROXY_IP_POLL_ATTEMPTS = 5
_PROXY_IP_POLL_INTERVAL = 0.25  # seconds (<=4 sleeps -> ~1s worst case)


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
        self._ca_cert = CACertDistributor(runner)
        self._credentials = ProxyCredentialManager(runner)
        self._state = ProxyStateStore(runner)
        self._endpoint_state = ProxyStateStore(
            runner,
            path="/data/auth/allowed-endpoints.json",
            schema="allowed-endpoints.v1",
            field="endpoints",
            description="allowed-endpoint",
        )

    def _create_credential_secrets(
        self,
        session_name: str,
        credentials: ProxyCredentials | Mapping[str, str] | None,
    ) -> list[str]:
        """Create podman secrets for proxy credentials."""
        return self._credentials.create_secrets(session_name, credentials)

    def _credential_env(
        self,
        credentials: ProxyCredentials | Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Return extra plain (non-secret) env vars derived from credential signals."""
        return self._credentials.credential_env(credentials)

    def remove_credential_secrets(self, session_name: str) -> None:
        """Remove all podman secrets for a session's proxy credentials."""
        self._credentials.remove_secrets(session_name)

    def has_proxy(self, session_name: str) -> bool:
        """Check if a session has a proxy container."""
        return self._runner.container_exists(proxy_container_name(session_name))

    def get_config_from_labels(
        self, session_name: str
    ) -> tuple[str, list[str], list[str], list[int]] | None:
        """Read proxy configuration from the main container's labels.

        Returns:
            Tuple of (proxy_image, domains, endpoints, otel_ports) or None.
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
        durable_domains, durable_endpoints = self.read_policy_state(
            session_name, proxy_image
        )
        if durable_domains is not None:
            domains = durable_domains

        endpoints = [
            item for item in labels.get(PAUDE_LABEL_ENDPOINTS, "").split(",") if item
        ]
        if durable_endpoints is not None:
            endpoints = durable_endpoints

        otel_ports_str = labels.get(PAUDE_LABEL_OTEL_PORTS, "")
        otel_ports = [int(p) for p in otel_ports_str.split(",") if p]

        return (proxy_image, domains, endpoints, otel_ports)

    def read_policy_state(
        self, session_name: str, proxy_image: str | None
    ) -> tuple[list[str] | None, list[str] | None]:
        """Read committed domain and endpoint overrides in one helper."""
        if not proxy_image:
            return None, None
        return self._state.read_pair(
            self._endpoint_state,
            auth_volume_name(session_name),
            proxy_image,
        )

    def start_if_needed(
        self,
        session_name: str,
        credentials: ProxyCredentials | Mapping[str, str] | None = None,
    ) -> None:
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
        proxy_image, domains, endpoints, otel_ports = proxy_config
        nname = network_name(session_name)
        ca_vol = ca_volume_name(session_name)
        auth_vol = auth_volume_name(session_name)
        from paude.container.volume import VolumeManager

        volume_mgr = VolumeManager(self._runner.engine)
        if not volume_mgr.volume_exists(auth_vol):
            volume_mgr.create_volume(auth_vol)

        disable_dns = self._runner.engine.is_podman
        self._network_manager.create_internal_network(nname, disable_dns=disable_dns)

        # This network may already have the session's long-lived agent
        # container attached (created once at `paude create` time), so unlike
        # create_proxy's brand-new network it must not be torn down on a
        # transient IP-resolution failure — see _resolve_proxy_ip's docstring.
        proxy_ip = self._resolve_proxy_ip(
            nname, disable_dns, remove_network_on_failure=False
        )
        agent_ip = derive_agent_ip(proxy_ip) if proxy_ip else None
        dns = _get_host_dns(self._runner.engine)
        secret_refs = self._create_credential_secrets(session_name, credentials)
        credential_env = self._credential_env(credentials)

        print(f"Recreating missing proxy {pname}...", file=sys.stderr)
        self._proxy_runner.create_session_proxy(
            name=pname,
            image=proxy_image,
            network=nname,
            dns=dns,
            allowed_domains=domains,
            allowed_endpoints=endpoints,
            ip=proxy_ip,
            otel_ports=otel_ports,
            ca_volume=ca_vol,
            credentials=credentials,
            allowed_clients=agent_ip,
            secret_refs=secret_refs,
            credential_env=credential_env,
            auth_volume=auth_vol,
        )
        self._proxy_runner.start_session_proxy(pname)

    def start_proxy(self, session_name: str) -> None:
        """Start the proxy container for a session."""
        pname = proxy_container_name(session_name)
        self._proxy_runner.start_session_proxy(pname)

    def distribute_ca_cert(self, session_name: str) -> None:
        """Copy the proxy's CA certificate into the agent container."""
        self._ca_cert.distribute(session_name)

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

        The gateway lookup is polled a few times because
        ``network inspect`` run right after ``network create`` can briefly
        report the network before its subnet/IPAM is populated (a race that
        widens under CI load). Returns None only if no gateway/subnet shows
        up within the bounded retry budget.
        """
        for attempt in range(_PROXY_IP_POLL_ATTEMPTS):
            gateway = self._network_manager.get_network_gateway(nname)
            if gateway:
                return NetworkManager.derive_proxy_ip(gateway)
            if attempt < _PROXY_IP_POLL_ATTEMPTS - 1:
                time.sleep(_PROXY_IP_POLL_INTERVAL)
        return None

    def _resolve_proxy_ip(
        self,
        nname: str,
        disable_dns: bool,
        *,
        remove_network_on_failure: bool,
    ) -> str | None:
        """Derive the proxy IP, failing loudly when it would be unreachable.

        On a --disable-dns network the agent container can't resolve the
        proxy by name, so a None IP means the proxy is genuinely
        unreachable. Raise ProxyStartError instead of silently degrading to
        an unusable session. With DNS the hostname fallback is legitimate,
        so None passes through unchanged.

        This invariant is a property of the network, not of any one caller,
        so every path that derives a proxy IP routes through here rather
        than re-implementing (and drifting from) the guard.

        Args:
            remove_network_on_failure: Remove the network before raising.
                Callers that just created the network pass True; callers
                operating on a live network (update_domains) pass False so
                a transient inspect failure doesn't tear down a working
                session's network.
        """
        proxy_ip = self._get_proxy_ip(nname)
        if proxy_ip is None and disable_dns:
            if remove_network_on_failure:
                self._network_manager.remove_network(nname)
            raise ProxyStartError(
                f"Could not determine proxy IP for network {nname}; the "
                "proxy would be unreachable on a --disable-dns network. "
                "Aborting."
            )
        return proxy_ip

    def create_proxy(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None,
        otel_ports: list[int] | None = None,
        credentials: ProxyCredentials | Mapping[str, str] | None = None,
        allowed_endpoints: list[str] | None = None,
    ) -> tuple[str, str | None]:
        """Create a proxy container for a session.

        Returns:
            Tuple of (network_name, proxy_ip). proxy_ip is None if the
            network gateway could not be determined.
        """
        if not proxy_image:
            raise ValueError("proxy_image is required to create a proxy")

        nname = network_name(session_name)
        # `disable_dns` is the single fact that decides whether a hostname
        # fallback is viable: with DNS the agent can resolve the proxy by
        # name, without it only the IP works. Derive it once and reuse it for
        # both the network create and the reachability guard in
        # _resolve_proxy_ip so the two can't drift.
        disable_dns = self._runner.engine.is_podman
        self._network_manager.create_internal_network(nname, disable_dns=disable_dns)

        proxy_ip = self._resolve_proxy_ip(
            nname, disable_dns, remove_network_on_failure=True
        )

        # Create a named volume for the CA certificate
        from paude.container.volume import VolumeManager

        ca_vol = ca_volume_name(session_name)
        auth_vol = auth_volume_name(session_name)
        volume_mgr = VolumeManager(self._runner.engine)
        volume_mgr.create_volume(ca_vol)
        auth_volume_created = False
        if not volume_mgr.volume_exists(auth_vol):
            volume_mgr.create_volume(auth_vol)
            auth_volume_created = True

        # Compute expected agent IP for source IP filtering
        agent_ip = derive_agent_ip(proxy_ip) if proxy_ip else None

        pname = proxy_container_name(session_name)
        dns = _get_host_dns(self._runner.engine)

        secret_refs = self._create_credential_secrets(session_name, credentials)
        credential_env = self._credential_env(credentials)

        print(f"Creating proxy {pname}...", file=sys.stderr)
        try:
            self._proxy_runner.create_session_proxy(
                name=pname,
                image=proxy_image,
                network=nname,
                dns=dns,
                allowed_domains=allowed_domains,
                allowed_endpoints=allowed_endpoints,
                ip=proxy_ip,
                otel_ports=otel_ports,
                ca_volume=ca_vol,
                credentials=credentials,
                allowed_clients=agent_ip,
                secret_refs=secret_refs,
                credential_env=credential_env,
                auth_volume=auth_vol,
            )
        except Exception:
            volume_mgr.remove_volume(ca_vol, force=True)
            if auth_volume_created:
                volume_mgr.remove_volume(auth_vol, force=True)
            self.remove_credential_secrets(session_name)
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

    def get_allowed_endpoints(self, session_name: str) -> list[str] | None:
        """Get current destination-scoped port exceptions for a session."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return None
        endpoints = self._runner.get_container_env(pname, "ALLOWED_ENDPOINTS")
        return [item for item in (endpoints or "").split(",") if item]

    def _endpoint_update_proxy_image(self, session_name: str) -> str:
        """Return the configured image after verifying endpoint capability."""
        container = find_container_by_session_name(self._runner, session_name)
        labels = container.get("Labels", {}) or {} if container else {}
        if PAUDE_LABEL_ENDPOINTS not in labels:
            raise ValueError(
                f"Session '{session_name}' predates allowed-endpoints support. "
                f"Run 'paude upgrade {session_name}' before changing endpoints."
            )
        proxy_image = labels.get(PAUDE_LABEL_PROXY_IMAGE, "")
        if not proxy_image:
            raise ValueError(f"Cannot inspect proxy configuration for: {session_name}")
        return str(proxy_image)

    def get_blocked_log(self, session_name: str) -> str | None:
        """Get raw blocked-domain log from the proxy container."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return None

        if not self._runner.container_running(pname):
            raise ValueError(f"Proxy for session '{session_name}' is not running.")

        result = self._runner.exec_in_container(
            pname, ["cat", PROXY_BLOCKED_LOG_PATH], check=False
        )
        if result.returncode != 0:
            return ""
        return result.stdout

    def _redistribute_ca_if_needed(self, session_name: str) -> None:
        """Verify the CA cert is still valid after a proxy recreate."""
        self._ca_cert.redistribute_if_needed(session_name)

    def update_domains(
        self,
        session_name: str,
        domains: list[str],
        credentials: ProxyCredentials | Mapping[str, str] | None = None,
        credential_targets: set[str] | None = None,
        required_credentials: set[str] | None = None,
        allowed_endpoints: list[str] | None = None,
        proxy_image: str | None = None,
        operation_label: str = "domains",
    ) -> None:
        """Update domains using preserved credentials and a rollback-safe swap."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            raise ValueError(
                f"Session '{session_name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        running_proxy_image = self._runner.get_container_image(pname)
        if not running_proxy_image:
            raise ValueError(f"Cannot inspect proxy container: {pname}")
        proxy_image = proxy_image or running_proxy_image

        # Preserve OTEL ports from labels across proxy recreate
        proxy_config = self.get_config_from_labels(session_name)
        _, _, configured_endpoints, otel_ports = (
            proxy_config if proxy_config else ("", [], [], [])
        )
        endpoints = (
            configured_endpoints if allowed_endpoints is None else allowed_endpoints
        )

        nname = network_name(session_name)
        ca_vol = ca_volume_name(session_name)
        auth_vol = auth_volume_name(session_name)
        # The network is already live here, so don't remove it on a
        # transient inspect failure — that would tear down a working
        # session's network.
        proxy_ip = self._resolve_proxy_ip(
            nname,
            self._runner.engine.is_podman,
            remove_network_on_failure=False,
        )
        agent_ip = derive_agent_ip(proxy_ip) if proxy_ip else None
        dns = _get_host_dns(self._runner.engine)

        if credentials is None:
            credentials = ProxyCredentials()
        elif not isinstance(credentials, ProxyCredentials):
            credentials = ProxyCredentials(environment=dict(credentials))
        commit_endpoint_state = allowed_endpoints is not None or bool(
            configured_endpoints
        )
        previous_domains, previous_endpoints = self._state.read_pair(
            self._endpoint_state, auth_vol, proxy_image
        )
        prepared = self._credentials.prepare_update(
            session_name,
            pname,
            credentials,
            credential_targets or set(),
            required_credentials or set(),
        )
        credential_env = self._credential_env(prepared.credentials)

        print(
            f"Updating proxy {operation_label} for session '{session_name}'...",
            file=sys.stderr,
        )
        swap = None
        try:
            swap = self._proxy_runner.swap_session_proxy(
                name=pname,
                image=proxy_image,
                network=nname,
                dns=dns,
                allowed_domains=domains,
                allowed_endpoints=endpoints,
                ip=proxy_ip,
                otel_ports=otel_ports,
                ca_volume=ca_vol,
                credentials=prepared.credentials,
                allowed_clients=agent_ip,
                secret_refs=prepared.secret_refs,
                credential_env=credential_env,
                auth_volume=auth_vol,
            )
            self._state.write(auth_vol, proxy_image, domains)
            if commit_endpoint_state:
                self._endpoint_state.write(auth_vol, proxy_image, endpoints)
            swap.commit()
        except Exception as primary:
            rollback_failures: list[str] = []
            if swap is not None:
                try:
                    self._state.restore(auth_vol, proxy_image, previous_domains)
                except Exception as exc:
                    rollback_failures.append(f"state restore failed: {exc}")
                if commit_endpoint_state:
                    try:
                        self._endpoint_state.restore(
                            auth_vol, proxy_image, previous_endpoints
                        )
                    except Exception as exc:
                        rollback_failures.append(
                            f"endpoint state restore failed: {exc}"
                        )
                try:
                    swap.rollback()
                except Exception as exc:
                    rollback_failures.append(f"proxy restore failed: {exc}")
            self._credentials.rollback_update(prepared)
            if rollback_failures:
                raise ProxyStartError(
                    f"Proxy update failed: {primary}; " + "; ".join(rollback_failures)
                ) from primary
            raise

        self._credentials.commit_update(prepared)

        # Verify CA cert survived the recreate (same named volume = same cert).
        # If the cert is missing or changed, redistribute to the agent.
        self._redistribute_ca_if_needed(session_name)

    def update_endpoints(
        self,
        session_name: str,
        endpoints: list[str],
        *,
        credentials: ProxyCredentials | None = None,
        credential_targets: set[str] | None = None,
        required_credentials: set[str] | None = None,
    ) -> None:
        """Update endpoints while preserving domains via the transactional swap."""
        proxy_image = self._endpoint_update_proxy_image(session_name)
        domains = self.get_allowed_domains(session_name)
        if domains is None:
            raise ValueError(
                f"Session '{session_name}' has no proxy (unrestricted network). "
                "Cannot update endpoints."
            )
        self.update_domains(
            session_name,
            domains,
            credentials=credentials,
            credential_targets=credential_targets,
            required_credentials=required_credentials,
            allowed_endpoints=endpoints,
            proxy_image=proxy_image,
            operation_label="endpoints",
        )
        from paude.endpoints import warn_for_uncovered_allowed_endpoints

        warn_for_uncovered_allowed_endpoints(
            endpoints,
            domains,
            session_name=session_name,
        )
