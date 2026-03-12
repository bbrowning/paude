"""Proxy management for the Podman backend."""

from __future__ import annotations

import sys

from paude.backends.podman.helpers import (
    find_container_by_session_name,
    network_name,
    proxy_container_name,
)
from paude.backends.shared import (
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_PROXY_IMAGE,
    SQUID_BLOCKED_LOG_PATH,
)
from paude.container.network import NetworkManager
from paude.container.runner import ContainerRunner
from paude.platform import get_podman_machine_dns


class PodmanProxyManager:
    """Manages proxy containers for Podman sessions."""

    def __init__(
        self,
        runner: ContainerRunner,
        network_manager: NetworkManager,
    ) -> None:
        self._runner = runner
        self._network_manager = network_manager

    def has_proxy(self, session_name: str) -> bool:
        """Check if a session has a proxy container."""
        return self._runner.container_exists(proxy_container_name(session_name))

    def get_config_from_labels(self, session_name: str) -> tuple[str, list[str]] | None:
        """Read proxy configuration from the main container's labels.

        Returns:
            Tuple of (proxy_image, domains) if proxy was configured,
            None if session has no proxy configuration.
        """
        container = find_container_by_session_name(self._runner, session_name)
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

    def start_if_needed(self, session_name: str) -> None:
        """Start or recreate the proxy container for a session.

        If the proxy container exists but is stopped, starts it.
        If the proxy container is missing but was expected (based on main
        container labels), recreates it from stored configuration.
        """
        pname = proxy_container_name(session_name)

        if self._runner.container_exists(pname):
            if self._runner.container_running(pname):
                return
            print(f"Starting proxy {pname}...", file=sys.stderr)
            self._runner.start_session_proxy(pname)
            return

        # Proxy doesn't exist — check if it was expected
        proxy_config = self.get_config_from_labels(session_name)
        if proxy_config is None:
            return  # No proxy expected for this session

        # Recreate the missing proxy
        proxy_image, domains = proxy_config
        nname = network_name(session_name)

        # Ensure network exists (create_internal_network is idempotent)
        self._network_manager.create_internal_network(nname)

        dns = get_podman_machine_dns()
        print(f"Recreating missing proxy {pname}...", file=sys.stderr)
        self._runner.create_session_proxy(
            name=pname,
            image=proxy_image,
            network=nname,
            dns=dns,
            allowed_domains=domains,
        )
        self._runner.start_session_proxy(pname)

    def stop_if_needed(self, session_name: str) -> None:
        """Stop the proxy container for a session if one exists."""
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return

        if not self._runner.container_running(pname):
            return

        self._runner.stop_container(pname)

    def create_proxy(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None,
    ) -> str:
        """Create a proxy container for a session.

        Creates the internal network and proxy container (stopped).

        Args:
            session_name: Session name.
            proxy_image: Proxy container image.
            allowed_domains: Domains to allow.

        Returns:
            Network name for the proxy.

        Raises:
            ValueError: If proxy_image is not provided.
        """
        if not proxy_image:
            raise ValueError("proxy_image is required when allowed_domains is set")

        nname = network_name(session_name)
        self._network_manager.create_internal_network(nname)

        pname = proxy_container_name(session_name)
        dns = get_podman_machine_dns()
        print(f"Creating proxy {pname}...", file=sys.stderr)
        try:
            self._runner.create_session_proxy(
                name=pname,
                image=proxy_image,
                network=nname,
                dns=dns,
                allowed_domains=allowed_domains,
            )
        except Exception:
            self._network_manager.remove_network(nname)
            raise

        return nname

    def get_allowed_domains(self, session_name: str) -> list[str] | None:
        """Get current allowed domains for a session.

        Returns None if the session has no proxy (unrestricted network).
        """
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            return None  # No proxy = unrestricted

        domains_str = self._runner.get_container_env(pname, "ALLOWED_DOMAINS")
        if not domains_str:
            return []

        return [d for d in domains_str.split(",") if d]

    def get_blocked_log(self, session_name: str) -> str | None:
        """Get raw squid blocked log from the proxy container.

        Returns:
            Raw log content, empty string if no blocks yet,
            or None if no proxy (unrestricted).

        Raises:
            ValueError: If proxy is not running.
        """
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
        """Update allowed domains for a session.

        Recreates the proxy container with the new domain list.

        Raises:
            ValueError: If session has no proxy deployment.
        """
        pname = proxy_container_name(session_name)
        if not self._runner.container_exists(pname):
            raise ValueError(
                f"Session '{session_name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        # Get proxy image from the proxy container
        proxy_image = self._runner.get_container_image(pname)
        if not proxy_image:
            raise ValueError(f"Cannot inspect proxy container: {pname}")

        nname = network_name(session_name)
        dns = get_podman_machine_dns()

        print(
            f"Updating proxy domains for session '{session_name}'...",
            file=sys.stderr,
        )
        self._runner.recreate_session_proxy(
            name=pname,
            image=proxy_image,
            network=nname,
            dns=dns,
            allowed_domains=domains,
        )
