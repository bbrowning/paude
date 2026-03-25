"""Tests for paude upgrade command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from paude.backends.base import Session
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
    encode_path,
)
from paude.cli import app
from paude.registry import RegistryEntry, SessionRegistry

runner = CliRunner()


def _make_session(
    name: str,
    status: str = "stopped",
    version: str | None = None,
    backend_type: str = "podman",
    workspace: Path | None = None,
) -> Session:
    return Session(
        name=name,
        status=status,
        workspace=workspace or Path(f"/home/user/{name}"),
        created_at="2026-01-01T00:00:00Z",
        backend_type=backend_type,
        version=version,
    )


class TestUpgradeCommand:
    """Tests for the session_upgrade CLI command."""

    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_session_not_found(self, mock_find: MagicMock) -> None:
        """Session doesn't exist, should print error and exit 1."""
        mock_find.return_value = None

        result = runner.invoke(app, ["upgrade", "nonexistent"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "not found" in output

    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_already_up_to_date(self, mock_find: MagicMock) -> None:
        """Session version matches current __version__, should print up to date."""
        from paude import __version__

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = _make_session(
            "test-session", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        output = result.stdout + (result.stderr or "")
        assert "already at version" in output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_already_up_to_date_with_rebuild(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """Same version but --rebuild=True should proceed with upgrade."""
        from paude import __version__

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = _make_session(
            "test-session", version=__version__
        )
        # Make backend appear as PodmanBackend
        from paude.backends.podman.backend import PodmanBackend

        mock_backend.__class__ = PodmanBackend
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session", "--rebuild"])

        assert result.exit_code == 0
        mock_upgrade_podman.assert_called_once()

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_auto_stops_running_session(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """Session is running, upgrade should call stop_session first."""
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="running", version="0.1.0"
        )
        from paude.backends.podman.backend import PodmanBackend

        mock_backend.__class__ = PodmanBackend
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        mock_backend.stop_session.assert_called_once_with("test-session")


class TestUpgradePodman:
    """Tests for _upgrade_podman internal function."""

    def _make_container_labels(
        self,
        workspace: Path | None = None,
        agent: str = "claude",
        domains: str | None = None,
        gpu: str | None = None,
        yolo: bool = False,
        proxy_image: str | None = None,
    ) -> dict[str, str]:
        ws = workspace or Path("/home/user/project")
        labels: dict[str, str] = {
            PAUDE_LABEL_AGENT: agent,
            PAUDE_LABEL_WORKSPACE: encode_path(ws, url_safe=True),
            PAUDE_LABEL_SESSION: "test-session",
            PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
        }
        if domains is not None:
            labels[PAUDE_LABEL_DOMAINS] = domains
        if gpu is not None:
            labels[PAUDE_LABEL_GPU] = gpu
        if yolo:
            labels[PAUDE_LABEL_YOLO] = "1"
        if proxy_image is not None:
            labels[PAUDE_LABEL_PROXY_IMAGE] = proxy_image
        return labels

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_preserves_volume(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """After upgrade, old container is removed but volume is NOT removed."""
        labels = self._make_container_labels()
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = (None, [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        # Old container and proxy container removed
        backend._runner.remove_container.assert_any_call(
            "paude-test-session", force=True
        )
        backend._runner.remove_container.assert_any_call(
            "paude-proxy-test-session", force=True
        )
        # create_session called with reuse_volume=True
        backend.create_session.assert_called_once()
        config = backend.create_session.call_args[0][0]
        assert config.reuse_volume is True
        # start_session_no_attach called
        backend.start_session_no_attach.assert_called_once_with("test-session")

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_reads_labels(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Labels are correctly read from old container and passed to SessionConfig."""
        labels = self._make_container_labels(
            agent="gemini",
            gpu="all",
            yolo=True,
            domains=".googleapis.com,.pypi.org",
            proxy_image="proxy:latest",
        )
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_custom_image.return_value = "paude:custom"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:rebuilt"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = (
            [".googleapis.com", ".pypi.org"],
            [],
            {},
            False,
        )

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        config = backend.create_session.call_args[0][0]
        assert config.agent == "gemini"
        assert config.gpu == "all"
        assert config.yolo is True
        assert config.name == "test-session"

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_rebuilds_image(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Image is rebuilt using ImageManager."""
        labels = self._make_container_labels()
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = (None, [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        mock_image_manager.ensure_default_image.assert_called_once()

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_removes_proxy(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Old proxy container and network are removed before creating new session."""
        labels = self._make_container_labels(
            domains=".googleapis.com",
            proxy_image="proxy:latest",
        )
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:rebuilt"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        # Proxy container removed
        backend._runner.remove_container.assert_any_call(
            "paude-proxy-test-session", force=True
        )
        # Network removed
        backend._network_manager.remove_network.assert_called_once_with(
            "paude-net-test-session"
        )

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_no_proxy(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Session without domains doesn't build proxy image."""
        labels = self._make_container_labels()  # No domains
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = (None, [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        # Proxy image not built
        mock_image_manager.ensure_proxy_image.assert_not_called()

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_podman_no_proxy_stays_unrestricted(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Session without domains must NOT gain a proxy after upgrade.

        Regression test: _prepare_session_create treats None domains as
        'use defaults', which would incorrectly add proxy filtering to a
        session that was originally unrestricted.
        """
        labels = self._make_container_labels()  # No PAUDE_LABEL_DOMAINS
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False)

        config = backend.create_session.call_args[0][0]
        assert config.allowed_domains is None, (
            "Unrestricted session must keep allowed_domains=None after upgrade"
        )


class TestListShowsVersion:
    """Tests for VERSION column in paude list."""

    @patch("paude.session_discovery.collect_all_sessions")
    def test_list_shows_version_column(self, mock_collect: MagicMock) -> None:
        """paude list output includes VERSION column."""
        session = _make_session("test-session", status="running", version="0.12.0")
        mock_collect.return_value = ([(session, MagicMock())], {"podman"})

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "VERSION" in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    def test_list_shows_outdated_indicator(self, mock_collect: MagicMock) -> None:
        """Sessions with version != current show * suffix."""
        session = _make_session("test-session", status="running", version="0.1.0")
        mock_collect.return_value = ([(session, MagicMock())], {"podman"})

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        # Should show version with * suffix for outdated
        assert "0.1.0*" in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    def test_list_no_outdated_indicator_when_current(
        self, mock_collect: MagicMock
    ) -> None:
        """Sessions at current version do NOT show * suffix."""
        from paude import __version__

        session = _make_session("test-session", status="running", version=__version__)
        mock_collect.return_value = ([(session, MagicMock())], {"podman"})

        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert f"{__version__}*" not in result.stdout
        assert __version__ in result.stdout


class TestRegistryBackwardCompat:
    """Tests for registry backward compatibility with paude_version field."""

    def test_registry_loads_without_paude_version(self, tmp_path: Path) -> None:
        """Existing registry JSON without paude_version loads fine."""
        path = tmp_path / "sessions.json"
        data = {
            "sessions": {
                "old-session": {
                    "name": "old-session",
                    "backend_type": "podman",
                    "workspace": "/home/user/old",
                    "agent": "claude",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            }
        }
        path.write_text(json.dumps(data))

        registry = SessionRegistry(path=path)
        entries = registry.load()

        assert "old-session" in entries
        assert entries["old-session"].paude_version is None

    def test_registry_register_with_version(self, tmp_path: Path) -> None:
        """register() stores paude_version."""
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = Session(
            name="versioned-session",
            status="stopped",
            workspace=Path("/home/user/project"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )

        registry.register(session, paude_version="0.13.0")

        entry = registry.get("versioned-session")
        assert entry is not None
        assert entry.paude_version == "0.13.0"

    def test_registry_version_survives_serialization(self, tmp_path: Path) -> None:
        """paude_version persists through save/load cycle."""
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = Session(
            name="v-session",
            status="stopped",
            workspace=Path("/home/user/project"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        registry.register(session, paude_version="0.13.0")

        # Reload from disk
        registry2 = SessionRegistry(path=path)
        entry = registry2.get("v-session")
        assert entry is not None
        assert entry.paude_version == "0.13.0"

    def test_registry_entry_to_session_includes_version(self) -> None:
        """RegistryEntry.to_session includes version field."""
        entry = RegistryEntry(
            name="test",
            backend_type="podman",
            workspace="/home/user/test",
            agent="claude",
            created_at="2026-01-01T00:00:00Z",
            paude_version="0.13.0",
        )
        session = entry.to_session(status="stopped")
        assert session.version == "0.13.0"


class TestUpgradeOpenShift:
    """Tests for _upgrade_openshift internal function."""

    def _make_statefulset(
        self,
        name: str = "test-session",
        agent: str = "claude",
        workspace: str = "L2hvbWUvdXNlci9wcm9qZWN0",  # base64 of /home/user/project
    ) -> dict:
        return {
            "metadata": {
                "name": f"paude-{name}",
                "labels": {
                    "app": "paude",
                    "paude.io/session-name": name,
                    PAUDE_LABEL_AGENT: agent,
                },
                "annotations": {
                    "paude.io/workspace": workspace,
                },
            },
        }

    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_openshift_patches_image(
        self,
        mock_detect_config: MagicMock,
    ) -> None:
        """StatefulSet is patched with new image."""
        import json

        from paude.backends.openshift import OpenShiftBackend

        backend = MagicMock(spec=OpenShiftBackend)
        backend.namespace = "test-ns"
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = self._make_statefulset()
        backend._lookup.has_proxy_deployment.return_value = False
        backend._lifecycle = MagicMock()
        backend._lifecycle._oc = MagicMock()
        backend._proxy = MagicMock()
        backend._pod_waiter = MagicMock()
        backend._syncer = MagicMock()
        backend.ensure_image_via_build.return_value = "registry.example.com/paude:new"

        from paude.cli.upgrade import _upgrade_openshift

        _upgrade_openshift(
            "test-session", backend, rebuild=False, openshift_context=None
        )

        # Verify oc patch was called
        oc = backend._lifecycle._oc
        patch_calls = [c for c in oc.run.call_args_list if c[0][0] == "patch"]
        assert len(patch_calls) == 1
        patch_arg = patch_calls[0][0][-1]  # last positional arg is the JSON patch
        parsed = json.loads(patch_arg)
        assert parsed[0]["op"] == "replace"
        assert parsed[0]["value"] == "registry.example.com/paude:new"

    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_openshift_updates_version_label(
        self,
        mock_detect_config: MagicMock,
    ) -> None:
        """Version label is updated on StatefulSet."""
        from paude import __version__
        from paude.backends.openshift import OpenShiftBackend
        from paude.backends.shared import PAUDE_LABEL_VERSION

        backend = MagicMock(spec=OpenShiftBackend)
        backend.namespace = "test-ns"
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = self._make_statefulset()
        backend._lookup.has_proxy_deployment.return_value = False
        backend._lifecycle = MagicMock()
        backend._lifecycle._oc = MagicMock()
        backend._proxy = MagicMock()
        backend._pod_waiter = MagicMock()
        backend._syncer = MagicMock()
        backend.ensure_image_via_build.return_value = "paude:new"

        from paude.cli.upgrade import _upgrade_openshift

        _upgrade_openshift(
            "test-session", backend, rebuild=False, openshift_context=None
        )

        oc = backend._lifecycle._oc
        label_calls = [c for c in oc.run.call_args_list if c[0][0] == "label"]
        assert len(label_calls) == 1
        assert f"{PAUDE_LABEL_VERSION}={__version__}" in label_calls[0][0]

    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_openshift_scales_proxy_when_present(
        self,
        mock_detect_config: MagicMock,
    ) -> None:
        """Proxy deployment is scaled up when it exists."""
        from paude.backends.openshift import OpenShiftBackend

        backend = MagicMock(spec=OpenShiftBackend)
        backend.namespace = "test-ns"
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = self._make_statefulset()
        backend._lookup.has_proxy_deployment.return_value = True
        backend._lifecycle = MagicMock()
        backend._lifecycle._oc = MagicMock()
        backend._proxy = MagicMock()
        backend._pod_waiter = MagicMock()
        backend._syncer = MagicMock()
        backend.ensure_image_via_build.return_value = "paude:new"

        from paude.cli.upgrade import _upgrade_openshift

        _upgrade_openshift(
            "test-session", backend, rebuild=False, openshift_context=None
        )

        backend._lifecycle._scale_deployment.assert_called_once()
        backend._proxy.wait_for_ready.assert_called_once_with("test-session")

    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_openshift_no_proxy_scaling_without_proxy(
        self,
        mock_detect_config: MagicMock,
    ) -> None:
        """No proxy scaling when proxy deployment doesn't exist."""
        from paude.backends.openshift import OpenShiftBackend

        backend = MagicMock(spec=OpenShiftBackend)
        backend.namespace = "test-ns"
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = self._make_statefulset()
        backend._lookup.has_proxy_deployment.return_value = False
        backend._lifecycle = MagicMock()
        backend._lifecycle._oc = MagicMock()
        backend._proxy = MagicMock()
        backend._pod_waiter = MagicMock()
        backend._syncer = MagicMock()
        backend.ensure_image_via_build.return_value = "paude:new"

        from paude.cli.upgrade import _upgrade_openshift

        _upgrade_openshift(
            "test-session", backend, rebuild=False, openshift_context=None
        )

        backend._lifecycle._scale_deployment.assert_not_called()
        backend._proxy.wait_for_ready.assert_not_called()

    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_openshift_resyncs_config(
        self,
        mock_detect_config: MagicMock,
    ) -> None:
        """Agent config is re-synced into the pod after upgrade."""
        from paude.backends.openshift import OpenShiftBackend

        backend = MagicMock(spec=OpenShiftBackend)
        backend.namespace = "test-ns"
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = self._make_statefulset()
        backend._lookup.has_proxy_deployment.return_value = False
        backend._lifecycle = MagicMock()
        backend._lifecycle._oc = MagicMock()
        backend._proxy = MagicMock()
        backend._pod_waiter = MagicMock()
        backend._syncer = MagicMock()
        backend.ensure_image_via_build.return_value = "paude:new"

        from paude.cli.upgrade import _upgrade_openshift

        _upgrade_openshift(
            "test-session", backend, rebuild=False, openshift_context=None
        )

        backend._syncer.sync_full_config.assert_called_once()
        call_kwargs = backend._syncer.sync_full_config.call_args
        assert call_kwargs[0][0] == "paude-test-session-0"  # pod_name
        assert call_kwargs[1]["agent_name"] == "claude"

    def test_upgrade_openshift_statefulset_not_found(self) -> None:
        """Error when StatefulSet not found."""
        from paude.backends.openshift import OpenShiftBackend

        backend = MagicMock(spec=OpenShiftBackend)
        backend._lookup = MagicMock()
        backend._lookup.get_statefulset.return_value = None

        import click

        from paude.cli.upgrade import _upgrade_openshift

        with pytest.raises(click.exceptions.Exit):
            _upgrade_openshift(
                "nonexistent", backend, rebuild=False, openshift_context=None
            )
