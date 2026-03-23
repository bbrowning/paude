"""Podman subprocess wrapper (deprecated — use ContainerEngine).

This module is kept for backward compatibility. New code should use
``ContainerEngine`` directly.
"""

from __future__ import annotations

import subprocess

from paude.container.engine import ContainerEngine

_default_engine = ContainerEngine("podman")


def run_podman(
    *args: str,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a podman command.

    .. deprecated::
        Use ``ContainerEngine.run()`` instead.
    """
    return _default_engine.run(*args, check=check, capture=capture)


def image_exists(tag: str) -> bool:
    """Check if a container image exists locally.

    .. deprecated::
        Use ``ContainerEngine.image_exists()`` instead.
    """
    return _default_engine.image_exists(tag)


def network_exists(name: str) -> bool:
    """Check if a podman network exists.

    .. deprecated::
        Use ``ContainerEngine.network_exists()`` instead.
    """
    return _default_engine.network_exists(name)
