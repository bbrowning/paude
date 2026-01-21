"""Podman subprocess wrapper."""

from __future__ import annotations

import subprocess


def run_podman(
    *args: str,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a podman command.

    Args:
        *args: Arguments to pass to podman.
        check: Raise on non-zero exit code.
        capture: Capture stdout/stderr.

    Returns:
        CompletedProcess result.

    Raises:
        subprocess.CalledProcessError: If check=True and command fails.
    """
    cmd = ["podman", *args]
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def image_exists(tag: str) -> bool:
    """Check if a container image exists locally.

    Args:
        tag: Image tag to check.

    Returns:
        True if image exists, False otherwise.
    """
    result = run_podman("image", "exists", tag, check=False)
    return result.returncode == 0


def network_exists(name: str) -> bool:
    """Check if a podman network exists.

    Args:
        name: Network name to check.

    Returns:
        True if network exists, False otherwise.
    """
    result = run_podman("network", "exists", name, check=False)
    return result.returncode == 0
