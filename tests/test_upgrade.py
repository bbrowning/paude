"""Tests for paude upgrade command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from paude.backends.base import Session
from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
)
from paude.backends.session_env import encode_path
from paude.cli import app
from paude.cli.upgrade import UpgradeOverrides
from paude.registry import RegistryEntry, SessionRegistry

_NO_OVERRIDES = UpgradeOverrides()

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

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_same_version_still_refreshes(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A same-version upgrade still refreshes agent tooling."""
        from paude import __version__
        from paude.backends.podman.backend import PodmanBackend

        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend
        mock_backend.get_session.return_value = _make_session(
            "test-session", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        mock_upgrade_podman.assert_called_once()
        assert mock_upgrade_podman.call_args.args[2] is True

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

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_passes_credential_and_mapping_overrides(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        from paude.backends.podman.backend import PodmanBackend

        mock_backend.__class__ = PodmanBackend
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(
            app,
            [
                "upgrade",
                "test-session",
                "--providers",
                "anthropic,openai",
                "--agent-provider",
                "claude=anthropic,codex=openai",
            ],
        )

        assert result.exit_code == 0
        overrides = mock_upgrade_podman.call_args.args[3]
        assert overrides.providers == ["anthropic", "openai"]
        assert overrides.agent_providers == {
            "claude": "anthropic",
            "codex": "openai",
        }


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

    def test_reused_volume_survives_container_creation_rollback(self) -> None:
        from paude.backends import SessionConfig
        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        config = SessionConfig(
            name="test-session",
            workspace=Path("/workspace"),
            image="paude:latest",
            reuse_volume=True,
        )

        PodmanBackend._rollback_session_resources(
            backend,
            config,
            "test-session",
            "paude-test-session-workspace",
            volume_reused=True,
        )

        backend._volume_manager.remove_volume.assert_not_called()

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

        mock_prepare.return_value = ([], [], {}, True)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False, overrides=_NO_OVERRIDES)

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
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False, overrides=_NO_OVERRIDES)

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

        mock_prepare.return_value = ([], [], {}, True)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False, overrides=_NO_OVERRIDES)

        mock_image_manager.ensure_default_image.assert_called_once_with(
            force_rebuild=True
        )

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
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False, overrides=_NO_OVERRIDES)

        # Proxy container removed
        backend._runner.remove_container.assert_any_call(
            "paude-proxy-test-session", force=True
        )
        # Network removed
        backend._network_manager.remove_network.assert_called_once_with(
            "paude-net-test-session"
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


class TestUpgradeOverrides:
    """Tests for config overrides during upgrade."""

    def test_has_changes_empty(self) -> None:
        """No changes when all fields are None."""
        overrides = UpgradeOverrides()
        assert overrides.has_changes() is False

    def test_has_changes_otel(self) -> None:
        overrides = UpgradeOverrides(otel_endpoint="http://collector:4318")
        assert overrides.has_changes() is True

    def test_has_changes_gpu(self) -> None:
        overrides = UpgradeOverrides(gpu="all")
        assert overrides.has_changes() is True

    def test_has_changes_yolo(self) -> None:
        overrides = UpgradeOverrides(yolo=True)
        assert overrides.has_changes() is True

    def test_has_changes_empty_string_gpu_disables(self) -> None:
        """Empty string for gpu means explicitly disabled, still a change."""
        overrides = UpgradeOverrides(gpu="")
        assert overrides.has_changes() is True


class TestUpgradePodmanWithOverrides:
    """Tests for _upgrade_podman with config overrides."""

    def _make_container_labels(
        self,
        workspace: Path | None = None,
        agent: str = "claude",
        domains: str | None = None,
        otel_endpoint: str | None = None,
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
        if otel_endpoint is not None:
            labels[PAUDE_LABEL_OTEL_ENDPOINT] = otel_endpoint
        return labels

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_adds_otel_endpoint(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --otel-endpoint stores it in SessionConfig."""
        labels = self._make_container_labels(
            domains=".googleapis.com",
        )
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(otel_endpoint="http://collector:4318")
        _upgrade_podman("test-session", backend, rebuild=False, overrides=overrides)

        config = backend.create_session.call_args[0][0]
        assert config.otel_endpoint == "http://collector:4318"
        assert config.otel_ports == [4318]

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_clears_otel_endpoint(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --otel-endpoint '' clears OTEL config."""
        labels = self._make_container_labels(
            domains=".googleapis.com",
            otel_endpoint="http://old-collector:4318",
        )
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(otel_endpoint="")
        _upgrade_podman("test-session", backend, rebuild=False, overrides=overrides)

        config = backend.create_session.call_args[0][0]
        assert config.otel_endpoint is None
        assert config.otel_ports == []

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_preserves_existing_otel(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade without --otel-endpoint preserves existing OTEL config."""
        labels = self._make_container_labels(
            domains=".googleapis.com",
            otel_endpoint="http://existing:4318",
        )
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman("test-session", backend, rebuild=False, overrides=_NO_OVERRIDES)

        config = backend.create_session.call_args[0][0]
        assert config.otel_endpoint == "http://existing:4318"
        assert config.otel_ports == [4318]

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_overrides_gpu(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --gpu overrides label value."""
        labels = self._make_container_labels()
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(gpu="all")
        _upgrade_podman("test-session", backend, rebuild=False, overrides=overrides)

        config = backend.create_session.call_args[0][0]
        assert config.gpu == "all"

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    @patch("paude.backends.podman.helpers.find_container_by_session_name")
    def test_upgrade_disables_gpu(
        self,
        mock_find_container: MagicMock,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --no-gpu (gpu='') disables GPU."""
        from paude.backends.labels import PAUDE_LABEL_GPU

        labels = self._make_container_labels()
        labels[PAUDE_LABEL_GPU] = "all"  # Had GPU before
        mock_find_container.return_value = {"Labels": labels}

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock(spec=PodmanBackend)
        backend._runner = MagicMock()
        backend._runner.container_exists.return_value = False
        backend._network_manager = MagicMock()
        backend._volume_manager = MagicMock()
        backend._engine = MagicMock()

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(gpu="")
        _upgrade_podman("test-session", backend, rebuild=False, overrides=overrides)

        config = backend.create_session.call_args[0][0]
        assert config.gpu is None
