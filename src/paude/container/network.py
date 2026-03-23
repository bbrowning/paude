"""Network management for paude containers."""

from __future__ import annotations

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
