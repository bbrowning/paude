"""Tests for paude upgrade command."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from paude.backends import PodmanBackend, SessionConfig
from paude.backends.base import Session
from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_AGENT_PROVIDERS,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_PROVIDERS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
)
from paude.backends.session_env import encode_path
from paude.cli import app
from paude.cli.upgrade import UpgradeOverrides
from paude.container.network import NetworkManager
from paude.container.volume import VolumeManager
from paude.registry import RegistryEntry, SessionRegistry
from tests.fakes import FakeTransport, make_backend, make_engine, make_runner

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
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
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

        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
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

        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
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

        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
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

    def _mock_podman_backend(self, mock_find: MagicMock) -> MagicMock:
        """Wire find_session_backend to return a stopped 0.1.0 podman session."""
        from paude.backends.podman.backend import PodmanBackend

        backend = MagicMock()
        backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_find.return_value = ("podman", backend)
        return backend

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_add_agent_sets_override(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        self._mock_podman_backend(mock_find)

        result = runner.invoke(app, ["upgrade", "test-session", "--add-agent", "codex"])

        assert result.exit_code == 0
        overrides = mock_upgrade_podman.call_args.args[3]
        assert overrides.add_agents == ["codex"]
        assert overrides.agents is None

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_add_agent_comma_and_repeatable(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        self._mock_podman_backend(mock_find)

        result = runner.invoke(
            app,
            ["upgrade", "test-session", "--add-agent", "codex,cursor"],
        )
        assert result.exit_code == 0
        assert mock_upgrade_podman.call_args.args[3].add_agents == ["codex", "cursor"]

        result = runner.invoke(
            app,
            [
                "upgrade",
                "test-session",
                "--add-agent",
                "codex",
                "--add-agent",
                "cursor",
            ],
        )
        assert result.exit_code == 0
        assert mock_upgrade_podman.call_args.args[3].add_agents == ["codex", "cursor"]

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_agents_full_set_and_agent_alias(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        self._mock_podman_backend(mock_find)

        result = runner.invoke(
            app, ["upgrade", "test-session", "--agents", "claude,codex"]
        )
        assert result.exit_code == 0
        assert mock_upgrade_podman.call_args.args[3].agents == ["claude", "codex"]

        result = runner.invoke(app, ["upgrade", "test-session", "--agent", "codex"])
        assert result.exit_code == 0
        assert mock_upgrade_podman.call_args.args[3].agents == ["codex"]

    def test_upgrade_add_agent_conflicts_with_agents(self) -> None:
        result = runner.invoke(
            app,
            [
                "upgrade",
                "test-session",
                "--add-agent",
                "codex",
                "--agents",
                "claude,codex",
            ],
        )
        assert result.exit_code == 1
        assert "--add-agent or --agent/--agents" in result.output

    def test_upgrade_agent_conflicts_with_agents(self) -> None:
        result = runner.invoke(
            app,
            [
                "upgrade",
                "test-session",
                "--agent",
                "claude",
                "--agents",
                "claude,codex",
            ],
        )
        assert result.exit_code == 1
        assert "--agent or --agents" in result.output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_upgrade_unknown_add_agent_fails_before_stop(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A typo'd agent name is rejected before the container is stopped."""
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="running", version="0.1.0"
        )
        from paude.backends.podman.backend import PodmanBackend

        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(
            app, ["upgrade", "test-session", "--add-agent", "coddex"]
        )

        assert result.exit_code == 1
        assert "Unknown agent" in result.output
        # The running session must not be torn down for an obvious typo.
        mock_backend.stop_session.assert_not_called()
        mock_upgrade_podman.assert_not_called()

    @pytest.mark.parametrize(
        ("extra_args", "expected"),
        [
            (["--add-agent", "coddex"], "Unknown agent"),
            (["--agents", "claude,bogus"], "Unknown agent"),
            (["--agent", "nope"], "Unknown agent"),
            (["--provider", "nope"], "Unknown provider"),
            (["--providers", "nope"], "Unknown provider"),
            (["--agent-provider", "claude=nope"], "Unknown provider"),
        ],
    )
    def test_upgrade_rejects_unknown_names(
        self, extra_args: list[str], expected: str
    ) -> None:
        """Unknown agent/provider names fail fast with a clear message."""
        result = runner.invoke(app, ["upgrade", "test-session", *extra_args])

        assert result.exit_code == 1
        assert expected in result.output


@dataclass
class _Upgrade:
    """A backend built from the shared factory, plus the doubles behind it.

    Replaces the ``MagicMock(spec=PodmanBackend)`` plus four private-attribute
    assignments this file used to need for every ``_upgrade_podman`` test.
    Those mirrored cli/upgrade.py reaching into the backend's collaborators
    (KNOWN_ISSUES TEST-004); now that it goes through ``backend.resources``,
    a real backend works and assertions land on the doubles the test injected
    rather than on attributes read back out of the system under test.
    """

    backend: PodmanBackend
    runner: MagicMock
    volumes: MagicMock
    networks: MagicMock
    create_session: MagicMock
    start: MagicMock

    @property
    def config(self) -> SessionConfig:
        """The SessionConfig the rebuild handed to create_session."""
        created: SessionConfig = self.create_session.call_args[0][0]
        return created


def _upgrade_backend(
    labels: dict[str, str] | None = None,
    *,
    container_exists: bool = False,
    remote: bool = False,
) -> _Upgrade:
    """Build a backend whose every collaborator is observable and inert."""
    from paude.transport.ssh import SshTransport

    transport = SshTransport("user@host") if remote else FakeTransport()
    runner = make_runner(
        make_engine(transport=transport, is_remote=remote),
        container_exists=container_exists,
        list_containers=[{"Labels": labels}] if labels is not None else [],
    )
    volumes = MagicMock(spec=VolumeManager)
    networks = MagicMock(spec=NetworkManager)
    backend = make_backend(runner, network_manager=networks, volume_manager=volumes)
    backend._proxy.read_policy_state = MagicMock(  # type: ignore[method-assign]
        return_value=(None, None)
    )
    create_session = MagicMock(return_value=_upgraded_session())
    start = MagicMock()
    backend.create_session = create_session  # type: ignore[method-assign]
    backend.start_session_no_attach = start  # type: ignore[method-assign]
    return _Upgrade(backend, runner, volumes, networks, create_session, start)


def _upgraded_session(name: str = "test-session") -> Session:
    return Session(
        name=name,
        status="stopped",
        workspace=Path("/home/user/project"),
        created_at="2026-01-01T00:00:00+00:00",
        backend_type="podman",
    )


class TestResolveBaseFromView:
    """Reading a session's config off its labels, before any override."""

    def test_legacy_session_without_a_providers_label_derives_them(self) -> None:
        """The spec carries the raw label; upgrade needs the derived set.

        A session created before the providers label existed has none, and
        rebuilding it with an empty credential set would provision nothing.
        The derivation also dedupes, which the raw agent-provider projection
        does not -- a gascity session maps two of its three agents to vertex.
        """
        from paude.backends.labels import LabeledSession, spec_from_labels
        from paude.cli.upgrade import _resolve_base_from_view

        view = LabeledSession(
            spec=spec_from_labels({PAUDE_LABEL_AGENT: "gascity"}),
            workspace=Path("/home/user/project"),
            created_at="2026-01-01T00:00:00+00:00",
            version=None,
        )

        state = _resolve_base_from_view(view)

        assert view.spec.credential_providers == []
        assert state.spec.credential_providers == ["vertex", "google"]

    def test_the_views_spec_is_copied_not_aliased(self) -> None:
        """LabeledSession is frozen; _apply_overrides mutates what it is given."""
        from paude.backends.labels import LabeledSession, spec_from_labels
        from paude.cli.upgrade import _resolve_base_from_view

        view = LabeledSession(
            spec=spec_from_labels({PAUDE_LABEL_AGENT: "claude"}),
            workspace=Path("/w"),
            created_at="",
            version=None,
        )

        state = _resolve_base_from_view(view)
        state.spec.gpu = "all"

        assert view.spec.gpu is None


class TestResolveBaseFromManifest:
    """Rebuilding a session's config from a persisted upgrade manifest."""

    def test_a_null_credential_providers_entry_resolves_to_empty(
        self, tmp_path: Path
    ) -> None:
        """The manifest is JSON on disk, and load applies no per-field checks.

        Only agent_providers is normalised on the way in, so a manifest holding
        ``"credential_providers": null`` -- hand-edited, or written by
        something other than paude -- constructs fine and reaches here intact.
        Without the fallback, resolving it raises TypeError and aborts the
        resumed upgrade instead of degrading to the empty set.
        """
        from paude import upgrade_state
        from paude.cli.upgrade import _resolve_base_from_manifest

        path = tmp_path / "upgrades.json"
        path.write_text(
            json.dumps(
                {
                    "upgrades": {
                        "sess": {
                            "name": "sess",
                            "to_version": "0.20.0",
                            "created_at": "2026-01-01T00:00:00+00:00",
                            "workspace": "/home/user/project",
                            "agent": "claude",
                            "credential_providers": None,
                        }
                    }
                }
            )
        )
        manifest = upgrade_state.load("sess", path=path)
        assert manifest is not None

        state = _resolve_base_from_manifest(manifest)

        assert state.spec.credential_providers == []


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
    def test_upgrade_podman_preserves_volume(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """After upgrade, old container is removed but volume is NOT removed."""
        labels = self._make_container_labels()

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([], [], {}, True)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        # Old container and proxy container removed
        up.runner.remove_container.assert_any_call("paude-test-session", force=True)
        up.runner.remove_container.assert_any_call(
            "paude-proxy-test-session", force=True
        )
        # create_session called with reuse_volume=True
        up.create_session.assert_called_once()
        config = up.config
        assert config.reuse_volume is True
        # start_session_no_attach called
        up.start.assert_called_once_with("test-session")

    @patch("paude.transport.config_sync.remap_mounts")
    @patch("paude.transport.config_sync.sync_configs_to_remote")
    @patch("paude.mounts.build_mounts")
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_remaps_remote_config_mounts(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
        mock_sync: MagicMock,
        mock_remap: MagicMock,
    ) -> None:
        """A --host upgrade transfers config to the remote host and remaps the
        bind-mount sources, so podman on the remote isn't handed a local (e.g.
        Mac) path that doesn't exist there ("statfs ...: no such file")."""

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager
        mock_prepare.return_value = ([], [], {}, True)

        local_mounts = ["-v", "/Users/bob/.gitconfig:/home/paude/.gitconfig:ro"]
        remote_mounts = [
            "-v",
            "/tmp/paude-config-x/0/.gitconfig:/home/paude/.gitconfig:ro",
        ]
        mock_build_mounts.return_value = local_mounts
        mock_sync.return_value = MagicMock(
            path_map={"/Users/bob/.gitconfig": "/tmp/paude-config-x/0/.gitconfig"}
        )
        mock_remap.return_value = remote_mounts

        up = _upgrade_backend(self._make_container_labels(), remote=True)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        mock_sync.assert_called_once()
        mock_remap.assert_called_once()
        # The recreated session uses the remapped (remote) mount sources.
        session_config = up.config
        assert session_config.mounts == remote_mounts

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_podman_reads_labels(
        self,
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

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        config = up.config
        assert config.agent == "gemini"
        assert config.gpu == "all"
        assert config.yolo is True
        assert config.name == "test-session"

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_podman_rebuilds_image(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Image is rebuilt using ImageManager."""
        labels = self._make_container_labels()

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([], [], {}, True)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        mock_image_manager.ensure_default_image.assert_called_once_with(
            force_rebuild=True
        )

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_podman_removes_proxy(
        self,
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

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:rebuilt"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        # Proxy container removed
        up.runner.remove_container.assert_any_call(
            "paude-proxy-test-session", force=True
        )
        # Network removed
        up.networks.remove_network.assert_called_once_with("paude-net-test-session")

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_podman_build_failure_is_retryable(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """A failed image build raises a plain exception (not typer.Exit), so the
        caller reports it as retryable; the manifest survives and nothing is torn
        down."""
        from paude import upgrade_state
        from paude.cli.upgrade import _upgrade_podman

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.side_effect = RuntimeError("boom")
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([], [], {}, True)

        up = _upgrade_backend(self._make_container_labels())

        # A plain error (typer.Exit is not a RuntimeError), so this asserts the
        # failure is routed to session_upgrade's "data is safe / retry" handler.
        with pytest.raises(RuntimeError):
            _upgrade_podman(
                "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
            )

        # The manifest (written before the build) survives, so a re-run resumes.
        assert upgrade_state.load("test-session") is not None
        up.create_session.assert_not_called()
        # And nothing was torn down: the images are built first precisely so a
        # build failure leaves the old session intact.
        up.runner.remove_container.assert_not_called()
        up.volumes.remove_volume.assert_not_called()
        up.networks.remove_network.assert_not_called()

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_proxy_build_failure_is_also_non_destructive(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """The proxy build is the last step before teardown, so it matters most.

        Both images are built before anything is removed; a failure on the
        second one must be as harmless as a failure on the first.
        """
        from paude import upgrade_state
        from paude.cli.upgrade import _upgrade_podman

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.side_effect = RuntimeError("no proxy")
        mock_image_manager_class.return_value = mock_image_manager
        mock_prepare.return_value = ([], [], {}, True)

        up = _upgrade_backend(self._make_container_labels())

        with pytest.raises(RuntimeError, match="building the proxy image failed"):
            _upgrade_podman(
                "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
            )

        assert upgrade_state.load("test-session") is not None
        up.runner.remove_container.assert_not_called()
        up.volumes.remove_volume.assert_not_called()
        up.create_session.assert_not_called()


class TestUpgradeResume:
    """Tests for crash-safe, resumable upgrade behaviour."""

    def _labels(
        self,
        agent: str = "claude",
        gpu: str | None = None,
        yolo: bool = False,
    ) -> dict[str, str]:
        labels: dict[str, str] = {
            PAUDE_LABEL_AGENT: agent,
            PAUDE_LABEL_WORKSPACE: encode_path(
                Path("/home/user/project"), url_safe=True
            ),
            PAUDE_LABEL_SESSION: "test-session",
            PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
        }
        if gpu is not None:
            labels[PAUDE_LABEL_GPU] = gpu
        if yolo:
            labels[PAUDE_LABEL_YOLO] = "1"
        return labels

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_manifest_written_before_teardown(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """A manifest is persisted before any destructive step, so an interrupt
        during teardown leaves a recoverable session."""
        from paude import upgrade_state
        from paude.cli.upgrade import _upgrade_podman

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:rebuilt"
        mock_image_manager_class.return_value = mock_image_manager
        mock_prepare.return_value = ([], [], {}, True)

        up = _upgrade_backend(self._labels(gpu="all", yolo=True))
        # Interrupt exactly at the first destructive removal.
        up.runner.remove_container.side_effect = KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            _upgrade_podman(
                "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
            )

        # Config was captured durably before teardown was attempted.
        manifest = upgrade_state.load("test-session")
        assert manifest is not None
        assert manifest.agent == "claude"
        assert manifest.gpu == "all"
        assert manifest.yolo is True
        # The replacement was never created (we were interrupted first).
        up.create_session.assert_not_called()

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_resume_uses_manifest_when_container_gone(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """With a manifest present and the old container gone, config is rebuilt
        from the manifest (not container labels) and the volume is reused."""
        from paude import upgrade_state
        from paude.cli.upgrade import _upgrade_podman
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version="0.20.0",
                created_at="2026-01-01T00:00:00+00:00",
                workspace="/home/user/project",
                agent="gemini",
                gpu="all",
                yolo=True,
                allowed_domains=[".googleapis.com"],
            )
        )

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:rebuilt"
        mock_image_manager_class.return_value = mock_image_manager
        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        up = _upgrade_backend()  # container already removed

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        config = up.config
        assert config.agent == "gemini"
        assert config.gpu == "all"
        assert config.yolo is True
        assert config.reuse_volume is True
        # Config came from the manifest, never from the (gone) container labels.
        up.runner.list_containers.assert_not_called()
        up.start.assert_called_once_with("test-session")

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_success_deletes_manifest(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A completed upgrade clears the manifest."""
        from paude import upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version="0.20.0",
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        mock_upgrade_podman.assert_called_once()
        assert upgrade_state.load("test-session") is None

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_stale_marker_cleared_when_already_current(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A leftover manifest for an already-current session is just cleared."""
        from paude import __version__, upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version=__version__,
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="running", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        mock_upgrade_podman.assert_not_called()
        assert upgrade_state.load("test-session") is None
        output = result.stdout + (result.stderr or "")
        assert "already at version" in output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_interrupted_between_create_and_start_resumes(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A manifest plus a target-version container left in 'stopped' state
        (created but never started) must resume, not silently clear the marker.

        _upgrade_podman creates the container before starting it; a
        created-but-not-started container reports podman state 'created', which
        maps to session status 'stopped'. The fast-path must not treat that as a
        finished upgrade.
        """
        from paude import __version__, upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version=__version__,
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="stopped", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        # The fast-path must NOT fire: the upgrade must actually resume.
        mock_upgrade_podman.assert_called_once()
        assert upgrade_state.load("test-session") is None
        output = result.stdout + (result.stderr or "")
        assert "cleared stale upgrade marker" not in output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_stale_marker_degraded_resumes(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A degraded session (running container, missing proxy) with a lingering
        manifest means the upgrade did not finish, so it must resume."""
        from paude import __version__, upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version=__version__,
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="degraded", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        mock_upgrade_podman.assert_called_once()
        assert upgrade_state.load("test-session") is None

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_stale_marker_refreshes_registry_version(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """The running fast-path refreshes the registry paude_version before
        clearing the marker, so a crash between _upgrade_podman finishing and the
        registry update doesn't leave the recorded version stale."""
        from paude import __version__, upgrade_state
        from paude.backends.base import Session
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        SessionRegistry().register(
            Session(
                name="test-session",
                status="running",
                workspace=Path("/w"),
                created_at="t",
                backend_type="podman",
            ),
            paude_version="0.1.0",
        )
        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version=__version__,
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", status="running", version=__version__
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        # Fast-path fired (no rebuild) ...
        mock_upgrade_podman.assert_not_called()
        # ... but the registry version was refreshed and the marker cleared.
        entry = SessionRegistry().get("test-session")
        assert entry is not None
        assert entry.paude_version == __version__
        assert upgrade_state.load("test-session") is None

    @patch("paude.cli.helpers._get_backend_instance")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_find_session_backend_offline_reconstructs(
        self, mock_podman_cls: MagicMock, mock_get_backend: MagicMock
    ) -> None:
        """find_session_backend(allow_offline=True) rebuilds from the registry
        when the session can't be discovered live."""
        from paude.backends.base import Session
        from paude.cli.helpers import find_session_backend
        from paude.registry import SessionRegistry

        SessionRegistry().register(
            Session(
                name="gone-session",
                status="stopped",
                workspace=Path("/w"),
                created_at="t",
                backend_type="podman",
            )
        )
        # Live probes find nothing (container gone).
        mock_podman_cls.return_value.get_session.return_value = None
        sentinel = object()
        mock_get_backend.return_value = sentinel

        # Without allow_offline the session is simply not found.
        assert find_session_backend("gone-session") is None

        # With allow_offline it is reconstructed from the registry entry.
        result = find_session_backend("gone-session", allow_offline=True)
        assert result is not None
        backend_type, backend = result
        assert backend is sentinel
        mock_get_backend.assert_called_with(backend_type, ssh_host=None, ssh_key=None)

    @patch("paude.cli.helpers._get_backend_instance")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_find_session_backend_offline_returns_none_on_bad_entry(
        self, mock_podman_cls: MagicMock, mock_get_backend: MagicMock
    ) -> None:
        """A malformed registry entry must not escape as a traceback: the
        offline reconstruction honors the documented 'returns None' contract."""
        from paude.backends.base import Session
        from paude.cli.helpers import find_session_backend
        from paude.registry import SessionRegistry

        SessionRegistry().register(
            Session(
                name="broken-session",
                status="stopped",
                workspace=Path("/w"),
                created_at="t",
                backend_type="podman",
            )
        )
        # Live probes find nothing (container gone).
        mock_podman_cls.return_value.get_session.return_value = None
        # Reconstruction blows up (e.g. parse_ssh_host on a malformed host).
        mock_get_backend.side_effect = ValueError("bad ssh host")

        assert find_session_backend("broken-session", allow_offline=True) is None

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_resume_allows_offline_backend_lookup(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A resume passes allow_offline=True and completes even when the
        container is gone (get_session returns None)."""
        from paude import upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version="0.20.0",
                created_at="t",
                workspace="/w",
            )
        )
        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = None  # container gone
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 0
        assert mock_find.call_args.kwargs.get("allow_offline") is True
        mock_upgrade_podman.assert_called_once()
        assert upgrade_state.load("test-session") is None

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_interrupt_preserves_manifest_and_reports(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """An interrupted upgrade keeps the manifest and tells the user how to
        finish it."""
        from paude import upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version="0.20.0",
                created_at="t",
                workspace="/w",
            )
        )
        mock_upgrade_podman.side_effect = KeyboardInterrupt

        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 130
        assert upgrade_state.load("test-session") is not None
        output = result.stdout + (result.stderr or "")
        assert "interrupted" in output.lower()
        assert "paude upgrade test-session" in output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_build_failure_reports_retry_and_keeps_manifest(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A failed upgrade (e.g. image build error) tells the user their data is
        safe and how to retry, and keeps the manifest so the resume can finish."""
        from paude import upgrade_state
        from paude.backends.podman.backend import PodmanBackend
        from paude.upgrade_state import UpgradeManifest

        upgrade_state.save(
            UpgradeManifest(
                name="test-session",
                to_version="0.20.0",
                created_at="t",
                workspace="/w",
            )
        )
        mock_upgrade_podman.side_effect = RuntimeError(
            "building the agent image failed: boom"
        )

        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 1
        # Manifest is preserved (cleared only on success), so a re-run resumes.
        assert upgrade_state.load("test-session") is not None
        output = result.stdout + (result.stderr or "")
        assert "safe" in output.lower()
        assert "retry" in output.lower()
        assert "paude upgrade test-session" in output

    @patch("paude.cli.upgrade._upgrade_podman")
    @patch("paude.cli.upgrade.find_session_backend")
    def test_failure_surfaces_called_process_stderr(
        self, mock_find: MagicMock, mock_upgrade_podman: MagicMock
    ) -> None:
        """A CalledProcessError escaping the upgrade must report its captured
        stderr, not the opaque 'returned non-zero exit status' string that hid
        the /pvc ownership migration failure."""
        from paude.backends.podman.backend import PodmanBackend

        mock_upgrade_podman.side_effect = subprocess.CalledProcessError(
            1, ["podman", "exec"], stderr="cp: '/pvc/.codex': Permission denied"
        )

        mock_backend = MagicMock()
        mock_backend.__class__ = PodmanBackend  # type: ignore[assignment]
        mock_backend.get_session.return_value = _make_session(
            "test-session", version="0.1.0"
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["upgrade", "test-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Permission denied" in output
        assert "returned non-zero exit status" not in output

    def test_unregister_clears_manifest(self) -> None:
        """Deleting a session (via registry.unregister) removes its manifest,
        so a stale marker can't outlive the session."""
        from paude import upgrade_state
        from paude.backends.base import Session
        from paude.registry import SessionRegistry
        from paude.upgrade_state import UpgradeManifest

        registry = SessionRegistry()
        registry.register(
            Session(
                name="doomed",
                status="stopped",
                workspace=Path("/w"),
                created_at="t",
                backend_type="podman",
            )
        )
        upgrade_state.save(
            UpgradeManifest(
                name="doomed", to_version="0.20.0", created_at="t", workspace="/w"
            )
        )
        assert upgrade_state.load("doomed") is not None

        registry.unregister("doomed")

        assert upgrade_state.load("doomed") is None


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

    def test_has_changes_add_agents(self) -> None:
        overrides = UpgradeOverrides(add_agents=["codex"])
        assert overrides.has_changes() is True

    def test_has_changes_agents(self) -> None:
        overrides = UpgradeOverrides(agents=["claude", "codex"])
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
    def test_upgrade_adds_otel_endpoint(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_prepare: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --otel-endpoint stores it in SessionConfig."""
        labels = self._make_container_labels(
            domains=".googleapis.com",
        )

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(otel_endpoint="http://collector:4318")
        _upgrade_podman("test-session", up.backend, rebuild=False, overrides=overrides)

        config = up.config
        assert config.otel_endpoint == "http://collector:4318"
        assert config.otel_ports == [4318]

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_clears_otel_endpoint(
        self,
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

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(otel_endpoint="")
        _upgrade_podman("test-session", up.backend, rebuild=False, overrides=overrides)

        config = up.config
        assert config.otel_endpoint is None
        assert config.otel_ports == []

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.cli.helpers._prepare_session_create")
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_preserves_existing_otel(
        self,
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

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
        mock_image_manager_class.return_value = mock_image_manager

        mock_prepare.return_value = ([".googleapis.com"], [], {}, False)

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(
            "test-session", up.backend, rebuild=False, overrides=_NO_OVERRIDES
        )

        config = up.config
        assert config.otel_endpoint == "http://existing:4318"
        assert config.otel_ports == [4318]

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_overrides_gpu(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --gpu overrides label value."""
        labels = self._make_container_labels()

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(gpu="all")
        _upgrade_podman("test-session", up.backend, rebuild=False, overrides=overrides)

        config = up.config
        assert config.gpu == "all"

    @patch("paude.mounts.build_mounts", return_value=[])
    @patch("paude.container.ImageManager")
    @patch("paude.config.detector.detect_config", return_value=None)
    def test_upgrade_disables_gpu(
        self,
        mock_detect_config: MagicMock,
        mock_image_manager_class: MagicMock,
        mock_build_mounts: MagicMock,
    ) -> None:
        """Upgrade with --no-gpu (gpu='') disables GPU."""
        from paude.backends.labels import PAUDE_LABEL_GPU

        labels = self._make_container_labels()
        labels[PAUDE_LABEL_GPU] = "all"  # Had GPU before

        mock_image_manager = MagicMock()
        mock_image_manager.ensure_default_image.return_value = "paude:latest"
        mock_image_manager_class.return_value = mock_image_manager

        up = _upgrade_backend(labels)

        from paude.cli.upgrade import _upgrade_podman

        overrides = UpgradeOverrides(gpu="")
        _upgrade_podman("test-session", up.backend, rebuild=False, overrides=overrides)

        config = up.config
        assert config.gpu is None


class TestUpgradePodmanAddAgent:
    """Tests for _upgrade_podman agent-set and provider mutation.

    Covers ``--add-agent`` / ``--agents`` (agent-set changes) and
    ``--provider`` / ``--agent-provider`` (provider swaps) on an existing
    session, exercised through a single mocked ``_upgrade_podman`` harness.
    """

    def _labels(
        self,
        specs: list[tuple[str, str]] | None = None,
        agent: str = "claude",
        providers: list[str] | None = None,
    ) -> dict[str, str]:
        """Container labels for a session, optionally with a full composition.

        ``providers`` seeds the credential-provider label (used to model a
        provider that is provisioned for the session but not mapped to any agent).
        """
        from paude.backends.labels import (
            encode_agent_providers,
            encode_providers,
        )

        labels: dict[str, str] = {
            PAUDE_LABEL_AGENT: agent,
            PAUDE_LABEL_WORKSPACE: encode_path(
                Path("/home/user/project"), url_safe=True
            ),
            PAUDE_LABEL_SESSION: "test-session",
            PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
        }
        if specs is not None:
            labels[PAUDE_LABEL_AGENT_PROVIDERS] = encode_agent_providers(specs)
        if providers is not None:
            labels[PAUDE_LABEL_PROVIDERS] = encode_providers(providers)
        return labels

    def _run(
        self, labels: dict[str, str], overrides: UpgradeOverrides
    ) -> SessionConfig:
        """Run _upgrade_podman with all I/O mocked; return the created config."""
        from paude.cli.upgrade import _upgrade_podman

        with (
            patch("paude.mounts.build_mounts", return_value=[]),
            patch(
                "paude.cli.helpers._prepare_session_create",
                return_value=([], [], {}, False),
            ),
            patch("paude.container.ImageManager") as mock_image_manager_class,
            patch("paude.config.detector.detect_config", return_value=None),
            patch(
                "paude.backends.podman.helpers.find_container_by_session_name",
                return_value={"Labels": labels},
            ),
        ):
            mock_image_manager = MagicMock()
            mock_image_manager.ensure_default_image.return_value = "paude:latest"
            mock_image_manager.ensure_proxy_image.return_value = "proxy:latest"
            mock_image_manager_class.return_value = mock_image_manager

            up = _upgrade_backend(labels)

            _upgrade_podman(
                "test-session", up.backend, rebuild=False, overrides=overrides
            )
            config: SessionConfig = up.config
            return config

    def test_add_agent_merges_composition_preserves_primary(self) -> None:
        """--add-agent codex appends codex, keeping claude primary."""
        config = self._run(self._labels(), UpgradeOverrides(add_agents=["codex"]))

        assert config.agent == "claude"
        assert config.agent_providers == [("claude", "vertex"), ("codex", "chatgpt")]
        # The new agent's provider is unioned onto the existing credential set.
        assert "vertex" in config.credential_providers
        assert "chatgpt" in config.credential_providers

    def test_add_agent_derives_provider_override(self) -> None:
        """--agent-provider selects the added agent's provider."""
        config = self._run(
            self._labels(),
            UpgradeOverrides(add_agents=["codex"], agent_providers={"codex": "openai"}),
        )

        assert ("codex", "openai") in config.agent_providers
        assert "openai" in config.credential_providers

    def test_add_existing_agent_is_noop(self) -> None:
        """Re-adding the primary agent does not duplicate it."""
        config = self._run(self._labels(), UpgradeOverrides(add_agents=["claude"]))

        assert config.agent_providers == [("claude", "vertex")]

    def test_add_agent_with_mapping_preserves_unmapped_credential(self) -> None:
        """--add-agent X --agent-provider X=P keeps credential-only providers.

        Regression: the combined add+remap workflow used to take the pure-remap
        branch and replace the credential set with only the mapped providers,
        silently dropping a provider that was provisioned but not mapped to any
        agent. It must union like a bare --add-agent instead.
        """
        config = self._run(
            # claude->vertex is mapped; openai is provisioned but unmapped.
            self._labels([("claude", "vertex")], providers=["vertex", "openai"]),
            UpgradeOverrides(
                add_agents=["gemini"], agent_providers={"gemini": "google"}
            ),
        )

        assert ("gemini", "google") in config.agent_providers
        assert "google" in config.credential_providers
        assert "vertex" in config.credential_providers
        # The unmapped, deliberately-provisioned provider survives.
        assert "openai" in config.credential_providers

    def test_add_agent_invalid_provider_rejected(self) -> None:
        """An unsupported agent/provider combo raises a friendly error."""
        with pytest.raises(ValueError, match="does not support provider"):
            self._run(
                self._labels(),
                UpgradeOverrides(
                    add_agents=["codex"], agent_providers={"codex": "vertex"}
                ),
            )

    def test_agent_provider_without_add_still_rejects_uninstalled(self) -> None:
        """--agent-provider for an agent that is neither installed nor added fails."""
        with pytest.raises(ValueError, match="are not installed"):
            self._run(
                self._labels(),
                UpgradeOverrides(agent_providers={"codex": "openai"}),
            )

    def test_agents_full_set_reorder_changes_primary(self) -> None:
        """--agents can reorder an existing set to change the primary."""
        config = self._run(
            self._labels([("claude", "vertex"), ("codex", "chatgpt")]),
            UpgradeOverrides(agents=["codex", "claude"]),
        )

        assert config.agent == "codex"
        assert config.agent_providers == [("codex", "chatgpt"), ("claude", "vertex")]

    def test_agents_dropping_installed_agent_rejected(self) -> None:
        """--agents that omits an installed agent is rejected (removal deferred)."""
        with pytest.raises(ValueError, match="Removing agents is not yet supported"):
            self._run(
                self._labels([("claude", "vertex"), ("codex", "chatgpt")]),
                UpgradeOverrides(agents=["codex"]),
            )

    def test_add_agent_round_trips_through_manifest(self) -> None:
        """The merged agent set is captured in the crash-recovery manifest."""
        from paude import upgrade_state

        self._run(self._labels(), UpgradeOverrides(add_agents=["codex"]))

        manifest = upgrade_state.load("test-session")
        assert manifest is not None
        assert manifest.agent_providers == [("claude", "vertex"), ("codex", "chatgpt")]

    @pytest.mark.parametrize(
        ("old", "new"), [("chatgpt", "openai"), ("openai", "chatgpt")]
    )
    def test_swap_codex_provider_in_place(self, old: str, new: str) -> None:
        """--agent-provider codex=NEW remaps codex and drops the OLD credential.

        Dropping the old provider (e.g. chatgpt) from the credential set is what
        turns off the proxy's ChatGPT-OAuth mode at next start.
        """
        config = self._run(
            self._labels([("claude", "vertex"), ("codex", old)]),
            UpgradeOverrides(agent_providers={"codex": new}),
        )

        assert config.agent == "claude"  # primary unchanged
        assert config.agent_providers == [("claude", "vertex"), ("codex", new)]
        assert new in config.credential_providers
        assert "vertex" in config.credential_providers
        assert old not in config.credential_providers

    def test_swap_via_provider_when_codex_primary(self) -> None:
        """--provider remaps the primary agent (codex here) from chatgpt to openai."""
        config = self._run(
            self._labels([("codex", "chatgpt")], agent="codex"),
            UpgradeOverrides(provider="openai"),
        )

        assert config.agent == "codex"
        assert config.agent_providers == [("codex", "openai")]
        assert config.provider == "openai"
        assert "openai" in config.credential_providers
        assert "chatgpt" not in config.credential_providers
