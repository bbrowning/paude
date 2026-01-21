"""Platform-specific code for paude."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def is_macos() -> bool:
    """Check if running on macOS.

    Returns:
        True if running on macOS, False otherwise.
    """
    return platform.system() == "Darwin"


def check_macos_volumes(workspace: Path, image: str) -> bool:
    """Check if workspace volumes work on macOS.

    On macOS, paths outside /Users/ require podman machine configuration.

    Args:
        workspace: Path to the workspace directory.
        image: Container image to test with.

    Returns:
        True if volumes work, False otherwise.
    """
    if not is_macos():
        return True

    # Check if workspace is outside /Users/
    if str(workspace).startswith("/Users/"):
        return True

    # Test volume mount
    try:
        result = subprocess.run(
            [
                "podman",
                "run",
                "--rm",
                "-v",
                f"{workspace}:{workspace}:rw",
                image,
                "--version",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            show_macos_volume_help(workspace)
            return False
    except subprocess.SubprocessError:
        show_macos_volume_help(workspace)
        return False

    return True


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


def show_macos_volume_help(workspace: Path) -> None:
    """Show help for macOS volume mount issues.

    Args:
        workspace: The workspace path that failed to mount.
    """
    print(
        f"""
Volume mount failed for path outside /Users/: {workspace}

On macOS, Podman runs in a VM and only mounts /Users/ by default.
To mount other paths, add them to your podman machine:

  1. Stop the machine:
     podman machine stop

  2. Edit the machine to add the mount:
     podman machine set --rootful

  3. Manually edit the machine config:
     # Location: ~/.config/containers/podman/machine/qemu/podman-machine-default.json
     # Add to "mounts" array:
     {{
       "source": "{workspace.parent}",
       "destination": "{workspace.parent}",
       "type": "virtiofs"
     }}

  4. Start the machine:
     podman machine start

Alternative: Work from a directory under /Users/.
""",
        file=sys.stderr,
    )
