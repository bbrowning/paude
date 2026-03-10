"""Platform-specific code for paude."""

from __future__ import annotations

import platform
import subprocess


def is_macos() -> bool:
    """Check if running on macOS.

    Returns:
        True if running on macOS, False otherwise.
    """
    return platform.system() == "Darwin"


def get_podman_machine_dns() -> str | None:
    """Get the DNS IP address from the podman machine VM.

    On macOS, containers run in a VM. The squid proxy needs the VM's
    DNS server IP to resolve external domains.

    Returns:
        DNS IP address string, or None if not on macOS or not available.
    """
    if not is_macos():
        return None

    try:
        # First check if a podman machine exists (matches bash behavior)
        inspect_result = subprocess.run(
            ["podman", "machine", "inspect"],
            capture_output=True,
            text=True,
        )
        if inspect_result.returncode != 0:
            return None

        # Get DNS IP from inside the podman VM's resolv.conf
        result = subprocess.run(
            ["podman", "machine", "ssh", "grep", "nameserver", "/etc/resolv.conf"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            # Parse "nameserver 192.168.x.x" to get the IP
            for line in result.stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    return parts[1]
    except subprocess.SubprocessError:
        pass

    return None
