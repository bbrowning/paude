"""SSH backend construction for paude."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.backends.podman.backend import PodmanBackend


def build_ssh_backend(
    entry: object,
    connect_timeout: int | None = None,
) -> PodmanBackend | None:
    """Reconstruct a PodmanBackend with SSH transport from a registry entry.

    Args:
        entry: A RegistryEntry (or any object) to inspect.
        connect_timeout: SSH connect timeout in seconds. Uses default if None.

    Returns:
        PodmanBackend configured with SSH transport, or None on failure.
    """
    from paude.container.engine import ContainerEngine
    from paude.registry import RegistryEntry
    from paude.transport.ssh import SSH_CONNECT_TIMEOUT, SshTransport, parse_ssh_host

    if not isinstance(entry, RegistryEntry) or not entry.ssh_host:
        return None

    host, port = parse_ssh_host(entry.ssh_host)
    transport = SshTransport(
        host,
        key=entry.ssh_key,
        port=port,
        connect_timeout=connect_timeout or SSH_CONNECT_TIMEOUT,
    )
    engine = ContainerEngine(entry.engine, transport=transport)
    try:
        from paude.backends import PodmanBackend

        return PodmanBackend(engine=engine)
    except Exception:  # noqa: S110
        return None
