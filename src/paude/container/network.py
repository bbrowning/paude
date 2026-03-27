"""Network management for paude containers."""

from __future__ import annotations

import ipaddress
import json
import sys

from paude.container.engine import ContainerEngine


class NetworkManager:
    """Manages container networks for paude."""

    def __init__(self, engine: ContainerEngine | None = None) -> None:
        self._engine = engine or ContainerEngine()

    def create_internal_network(self, name: str, disable_dns: bool = False) -> None:
        """Create an internal (no external access) network."""
        if not self._engine.network_exists(name):
            print(f"Creating {name} network...", file=sys.stderr)
            args = ["network", "create", "--internal"]
            if disable_dns:
                args.append("--disable-dns")
            args.append(name)
            self._engine.run(*args)

    def remove_network(self, name: str) -> None:
        """Remove a network."""
        if self._engine.network_exists(name):
            self._engine.run("network", "rm", name, check=False)

    def network_exists(self, name: str) -> bool:
        """Check if a network exists."""
        return self._engine.network_exists(name)

    def get_network_gateway(self, name: str) -> str | None:
        """Get the gateway IP of a network.

        Parses the JSON output of ``network inspect`` to support both
        Docker (``IPAM.Config[].Gateway``) and Podman
        (``subnets[].gateway``) formats.
        """
        result = self._engine.run(
            "network",
            "inspect",
            name,
            check=False,
        )
        if result.returncode != 0:
            return None
        return self._parse_gateway_json(result.stdout)

    @staticmethod
    def _parse_gateway_json(output: str) -> str | None:
        """Extract the gateway IP from network inspect JSON.

        Handles both Docker and Podman output formats, and tolerates
        the response being a JSON array or a single object.
        """
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return None

        if isinstance(data, list):
            if not data:
                return None
            data = data[0]

        if not isinstance(data, dict):
            return None

        # Docker format: {"IPAM": {"Config": [{"Gateway": "..."}]}}
        ipam = data.get("IPAM")
        if isinstance(ipam, dict):
            configs = ipam.get("Config")
            if isinstance(configs, list):
                for cfg in configs:
                    gw: str = cfg.get("Gateway", "")
                    if gw:
                        return gw

        # Podman format: {"subnets": [{"gateway": "..."} or {"subnet": "..."}]}
        subnets = data.get("subnets")
        if isinstance(subnets, list):
            for subnet in subnets:
                gw = subnet.get("gateway", "")
                if gw:
                    return gw

            # No explicit gateway (e.g. --disable-dns internal networks).
            # Derive a synthetic gateway from the subnet so that
            # derive_proxy_ip() can assign the proxy a fixed IP via
            # --network net:ip=<gateway+1>.
            for subnet in subnets:
                cidr = subnet.get("subnet", "")
                if cidr:
                    try:
                        net = ipaddress.ip_network(cidr, strict=False)
                        return str(net.network_address + 1)
                    except ValueError:
                        continue

        return None

    @staticmethod
    def derive_proxy_ip(gateway: str) -> str:
        """Derive a fixed proxy IP from the network gateway.

        Returns the next IP after the gateway (e.g. 10.89.0.1 → 10.89.0.2).
        """
        return str(ipaddress.ip_address(gateway) + 1)
