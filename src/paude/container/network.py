"""Network management for paude containers."""

from __future__ import annotations

import sys

from paude.container.podman import network_exists, run_podman


class NetworkManager:
    """Manages podman networks for paude."""

    def create_internal_network(self, name: str) -> None:
        """Create an internal (no external access) network.

        Args:
            name: Network name.
        """
        if not network_exists(name):
            print(f"Creating {name} network...", file=sys.stderr)
            run_podman("network", "create", "--internal", name)

    def remove_network(self, name: str) -> None:
        """Remove a network.

        Args:
            name: Network name.
        """
        if network_exists(name):
            run_podman("network", "rm", name, check=False)

    def network_exists(self, name: str) -> bool:
        """Check if a network exists.

        Args:
            name: Network name.

        Returns:
            True if network exists.
        """
        return network_exists(name)
