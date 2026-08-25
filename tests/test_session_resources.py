"""Tests for SessionResources -- the three teardowns, side by side.

The point of reading them together: they are *not* the same operation, and the
difference is what stops an upgrade from deleting a user's workspace. Every
"never removes" assertion below is load-bearing, not defensive.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from paude.backends.base import SessionConfig
from paude.backends.labels import PAUDE_LABEL_AGENT, PAUDE_LABEL_SESSION
from paude.backends.podman.helpers import (
    auth_volume_name,
    ca_volume_name,
    container_name,
    network_name,
    proxy_container_name,
    volume_name,
)
from paude.backends.podman.proxy import PodmanProxyManager
from paude.backends.podman.resources import SessionResources
from paude.container.network import NetworkManager
from paude.container.volume import VolumeManager
from tests.fakes import make_runner

SESSION = "test-session"


@pytest.fixture
def volumes() -> MagicMock:
    return MagicMock(spec=VolumeManager)


@pytest.fixture
def networks() -> MagicMock:
    return MagicMock(spec=NetworkManager)


@pytest.fixture
def proxy() -> MagicMock:
    return MagicMock(spec=PodmanProxyManager)


@pytest.fixture
def runner() -> MagicMock:
    return make_runner()


@pytest.fixture
def resources(
    runner: MagicMock,
    networks: MagicMock,
    volumes: MagicMock,
    proxy: MagicMock,
) -> SessionResources:
    return SessionResources(runner, networks, volumes, proxy)


def _removed_volumes(volumes: MagicMock) -> list[str]:
    """Every volume name passed to either removal method."""
    return [
        call.args[0]
        for call in (
            *volumes.remove_volume.call_args_list,
            *volumes.remove_volume_verified.call_args_list,
        )
    ]


class TestTeardownForRebuild:
    """Upgrade's teardown: removes the container, keeps every byte of state."""

    def test_removes_container_proxy_network_and_ca_volume(
        self,
        resources: SessionResources,
        runner: MagicMock,
        networks: MagicMock,
        volumes: MagicMock,
    ) -> None:
        resources.teardown_for_rebuild(SESSION)

        runner.remove_container.assert_any_call(container_name(SESSION), force=True)
        runner.remove_container.assert_any_call(
            proxy_container_name(SESSION), force=True
        )
        networks.remove_network.assert_called_once_with(network_name(SESSION))
        volumes.remove_volume.assert_any_call(ca_volume_name(SESSION), force=True)

    def test_never_removes_the_workspace_volume(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        """The user's data. Losing this is the worst outcome in the codebase."""
        resources.teardown_for_rebuild(SESSION)

        assert volume_name(SESSION) not in _removed_volumes(volumes)
        volumes.remove_volume_verified.assert_not_called()

    def test_never_removes_the_proxy_auth_volume(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        """Live proxy OAuth state -- removing it makes the user log in again."""
        resources.teardown_for_rebuild(SESSION)

        assert auth_volume_name(SESSION) not in _removed_volumes(volumes)

    def test_never_clears_credential_secrets(
        self, resources: SessionResources, proxy: MagicMock, runner: MagicMock
    ) -> None:
        resources.teardown_for_rebuild(SESSION)

        proxy.remove_credential_secrets.assert_not_called()
        runner.remove_secret.assert_not_called()

    def test_every_removal_is_tolerant(
        self, resources: SessionResources, runner: MagicMock, volumes: MagicMock
    ) -> None:
        """Re-running after a partial teardown must converge, not fail.

        This is what makes an interrupted upgrade resumable.
        """
        resources.teardown_for_rebuild(SESSION)

        assert all(
            call.kwargs.get("force") is True
            for call in runner.remove_container.call_args_list
        )
        assert all(
            call.kwargs.get("force") is True
            for call in volumes.remove_volume.call_args_list
        )


class TestCleanupAll:
    """The delete path: this one is *meant* to destroy the session's data."""

    def test_removes_every_volume_and_secret(
        self,
        resources: SessionResources,
        runner: MagicMock,
        networks: MagicMock,
        volumes: MagicMock,
        proxy: MagicMock,
    ) -> None:
        volumes.volume_exists.return_value = True

        resources.cleanup_all(SESSION, volume_name(SESSION))

        networks.remove_network.assert_called_once_with(network_name(SESSION))
        removed = _removed_volumes(volumes)
        assert ca_volume_name(SESSION) in removed
        assert auth_volume_name(SESSION) in removed
        assert volume_name(SESSION) in removed
        proxy.remove_credential_secrets.assert_called_once_with(SESSION)
        runner.remove_secret.assert_called_once()

    def test_workspace_volume_removal_is_verified(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        """Unlike the rebuild teardown, a delete must confirm the data is gone."""
        resources.cleanup_all(SESSION, volume_name(SESSION))

        volumes.remove_volume_verified.assert_called_once_with(volume_name(SESSION))

    def test_skips_proxy_volumes_that_do_not_exist(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        volumes.volume_exists.return_value = False

        resources.cleanup_all(SESSION, volume_name(SESSION))

        assert ca_volume_name(SESSION) not in _removed_volumes(volumes)


class TestRollbackCreate:
    """Create's rollback: undoes a half-built session without eating a reused one."""

    def _config(self, *, proxy_image: str | None = "proxy:latest") -> SessionConfig:
        return SessionConfig(
            name=SESSION,
            workspace=Path("/workspace"),
            image="paude:latest",
            proxy_image=proxy_image,
        )

    def test_reused_volume_survives(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        """An upgrade rebuild reuses the workspace volume; a failed container
        recreate must not take the user's data with it."""
        resources.rollback_create(
            self._config(), SESSION, volume_name(SESSION), volume_reused=True
        )

        volumes.remove_volume.assert_not_called()

    def test_fresh_volume_is_removed(
        self, resources: SessionResources, volumes: MagicMock
    ) -> None:
        resources.rollback_create(
            self._config(), SESSION, volume_name(SESSION), volume_reused=False
        )

        volumes.remove_volume.assert_called_once_with(volume_name(SESSION), force=True)

    def test_proxyless_session_leaves_the_network_alone(
        self, resources: SessionResources, runner: MagicMock, networks: MagicMock
    ) -> None:
        resources.rollback_create(
            self._config(proxy_image=None),
            SESSION,
            volume_name(SESSION),
            volume_reused=False,
        )

        runner.remove_container.assert_not_called()
        networks.remove_network.assert_not_called()


class TestReads:
    """One container fetch, and the probes that do not need one."""

    def test_labels_reads_the_whole_spec_in_one_fetch(
        self, resources: SessionResources, runner: MagicMock
    ) -> None:
        runner.list_containers.return_value = [
            {
                "Id": "abc",
                "Labels": {PAUDE_LABEL_SESSION: SESSION, PAUDE_LABEL_AGENT: "codex"},
            }
        ]

        view = resources.labels(SESSION)

        assert view is not None
        assert view.spec.agent == "codex"
        assert runner.list_containers.call_count == 1

    def test_labels_is_none_for_a_session_with_no_container(
        self, resources: SessionResources, runner: MagicMock
    ) -> None:
        runner.list_containers.return_value = []

        assert resources.labels(SESSION) is None

    def test_probes_address_the_agent_container(
        self, resources: SessionResources, runner: MagicMock
    ) -> None:
        runner.container_exists.return_value = True
        runner.container_running.return_value = False
        runner.get_container_image.return_value = "paude:latest"

        assert resources.exists(SESSION) is True
        assert resources.running(SESSION) is False
        assert resources.image(SESSION) == "paude:latest"
        for probe in (
            runner.container_exists,
            runner.container_running,
            runner.get_container_image,
        ):
            probe.assert_called_once_with(container_name(SESSION))


class TestMigrateLegacyState:
    """Salvage is skipped when there is nothing left to salvage."""

    def test_skipped_when_the_old_container_is_gone(
        self, resources: SessionResources, runner: MagicMock
    ) -> None:
        """The resume path: its state was already copied into the volume."""
        runner.container_exists.return_value = False

        resources.migrate_legacy_state(SESSION, MagicMock())

        runner.start_container.assert_not_called()
        runner.exec_in_container.assert_not_called()
