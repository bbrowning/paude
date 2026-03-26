"""Network management for paude containers."""

from __future__ import annotations

import ipaddress
import sys

from paude.container.engine import ContainerEngine


class NetworkManager:
    """Manages container networks for paude."""

    def __init__(self, engine: ContainerEngine | None = None) -> None:
        self._engine = engine or ContainerEngine()

    def create_internal_network(self, name: str) -> None:
        """Create an internal (no external access) network."""
        if not self._engine.network_exists(name):
            print(f"Creating {name} network...", file=sys.stderr)
            self._engine.run("network", "create", "--internal", name)

    def remove_network(self, name: str) -> None:
        """Remove a network."""
        if self._engine.network_exists(name):
            self._engine.run("network", "rm", name, check=False)

    def network_exists(self, name: str) -> bool:
        """Check if a network exists."""
        return self._engine.network_exists(name)

    def get_network_gateway(self, name: str) -> str | None:
        """Get the gateway IP of a network."""
        result = self._engine.run(
            "network",
            "inspect",
            name,
            "--format",
            "{{range .IPAM.Config}}{{.Gateway}}{{end}}",
            check=False,
        )
        if result.returncode != 0:
            return None
        gw = result.stdout.strip()
        return gw or None

    @staticmethod
    def derive_proxy_ip(gateway: str) -> str:
        """Derive a fixed proxy IP from the network gateway.

        Returns the next IP after the gateway (e.g. 10.89.0.1 → 10.89.0.2).
        """
        return str(ipaddress.ip_address(gateway) + 1)
