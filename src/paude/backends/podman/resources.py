"""The resource-level view of a session's container, network and volumes.

Exists so ``paude upgrade`` and ``paude backup`` can drive a rebuild without
reaching into ``PodmanBackend``'s collaborators. It deliberately does *not*
expose the runner, network manager or volume manager: the point is that the set
of destructive operations a rebuild may perform is enumerable here, in the
backend, rather than assembled ad hoc from the CLI layer.

Three teardowns live side by side, and they are not the same operation. The
differences are the whole reason this module exists:

==========================  ============  ================  ====================
step                        cleanup_all   rollback_create   teardown_for_rebuild
==========================  ============  ================  ====================
proxy container             verified      if proxy_image    force
agent container             (caller)      --                force
network                     yes           if proxy_image    yes
CA volume                   if exists     --                force
**auth volume**             yes           --                **no**
**credential secrets**      yes           --                **no**
**workspace volume**        verified      if not reused     **no**
GCP ADC secret              yes           --                --
==========================  ============  ================  ====================

Collapsing them behind flags would put "delete the user's workspace" one wrong
default away from the upgrade path, so they stay three named verbs sharing only
the two lines they genuinely have in common.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import TYPE_CHECKING

from paude.backends.labels import LabeledSession, read_labels
from paude.backends.podman.helpers import (
    auth_volume_name,
    ca_volume_name,
    container_name,
    find_container_by_session_name,
    network_name,
    proxy_container_name,
)
from paude.constants import GCP_ADC_SECRET_NAME

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.backends.base import SessionConfig
    from paude.backends.podman.proxy import PodmanProxyManager
    from paude.container.network import NetworkManager
    from paude.container.runner import ContainerRunner
    from paude.container.volume import VolumeManager


class SessionResources:
    """Read and remove the podman objects that make up one session."""

    def __init__(
        self,
        runner: ContainerRunner,
        network_manager: NetworkManager,
        volume_manager: VolumeManager,
        proxy: PodmanProxyManager,
    ) -> None:
        self._runner = runner
        self._network_manager = network_manager
        self._volume_manager = volume_manager
        self._proxy = proxy

    # -- reads ------------------------------------------------------------

    def exists(self, name: str) -> bool:
        """Whether the session's agent container exists."""
        return self._runner.container_exists(container_name(name))

    def running(self, name: str) -> bool:
        """Whether the session's agent container is running."""
        return self._runner.container_running(container_name(name))

    def image(self, name: str) -> str | None:
        """The image the session's agent container was created from."""
        return self._runner.get_container_image(container_name(name))

    def labels(self, name: str) -> LabeledSession | None:
        """Read everything the session's labels record, in one container fetch.

        ``None`` when no container matches. One fetch matters: on a ``--host``
        session each one is an SSH round trip.
        """
        container = find_container_by_session_name(self._runner, name)
        if container is None:
            return None
        view = read_labels(container.get("Labels", {}) or {})
        domains, endpoints = self._proxy.read_policy_state(name, view.spec.proxy_image)
        if domains is None and endpoints is None:
            return view
        return replace(
            view,
            spec=replace(
                view.spec,
                allowed_domains=(
                    view.spec.allowed_domains if domains is None else domains
                ),
                allowed_endpoints=(
                    view.spec.allowed_endpoints if endpoints is None else endpoints
                ),
            ),
        )

    # -- rebuild ----------------------------------------------------------

    def migrate_legacy_state(self, name: str, composition: AgentComposition) -> None:
        """Salvage state written by an older image into the session volume.

        A no-op when the old container is already gone -- which is the case
        when resuming an interrupted upgrade, where its state was copied into
        the volume before it was removed.
        """
        from paude.backends.podman.legacy_state import migrate_legacy_state

        cname = container_name(name)
        if not self._runner.container_exists(cname):
            return
        print("Migrating persistent agent state...", file=sys.stderr)
        migrate_legacy_state(self._runner, cname, composition)

    def teardown_for_rebuild(self, name: str) -> None:
        """Remove the session's container, proxy, network and CA volume.

        Keeps everything that holds state: the **workspace volume** (the user's
        data), the **proxy auth volume** (live OAuth state -- see
        ``PodmanProxyManager``), and the **credential secrets**. A rebuild
        re-derives the rest from the host environment, exactly as a fresh
        create does, but it must not make the user log in again or lose work.

        Every removal is force/tolerant, so re-running after a partial teardown
        converges rather than failing. That is what makes an interrupted
        upgrade resumable.
        """
        cname = container_name(name)
        print(f"Removing old container {cname}...", file=sys.stderr)
        self._runner.remove_container(cname, force=True)
        self._remove_proxy_and_network(name)
        self._volume_manager.remove_volume(ca_volume_name(name), force=True)

    def rollback_create(
        self,
        config: SessionConfig,
        name: str,
        vname: str,
        volume_reused: bool,
    ) -> None:
        """Clean up proxy/volume on container creation failure."""
        if config.proxy_image:
            self._remove_proxy_and_network(name)
        if not volume_reused:
            self._volume_manager.remove_volume(vname, force=True)

    def cleanup_all(self, name: str, vname: str) -> None:
        """Remove network, proxy volumes, secrets, and the workspace volume.

        The delete path: unlike :meth:`teardown_for_rebuild` this destroys the
        session's data, including the workspace volume.
        """
        self._network_manager.remove_network(network_name(name))
        for pv in (ca_volume_name(name), auth_volume_name(name)):
            if self._volume_manager.volume_exists(pv):
                self._volume_manager.remove_volume(pv, force=True)
        self._proxy.remove_credential_secrets(name)
        print(f"Removing volume {vname}...", file=sys.stderr)
        self._volume_manager.remove_volume_verified(vname)
        self._runner.remove_secret(GCP_ADC_SECRET_NAME)

    def _remove_proxy_and_network(self, name: str) -> None:
        """Drop the proxy sidecar and its network, tolerantly."""
        self._runner.remove_container(proxy_container_name(name), force=True)
        self._network_manager.remove_network(network_name(name))
