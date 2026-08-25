"""Tests for the rebuild steps shared by create and upgrade."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.agents import get_agents
from paude.backends.labels import SessionSpec
from paude.cli.session_rebuild import (
    ImageBuildError,
    SessionImages,
    build_session_images,
    prepare_session_mounts,
    session_config_from_spec,
)
from paude.config.models import PaudeConfig
from paude.transport.config_sync import RemoteConfigPaths
from paude.transport.ssh import SshTransport
from tests.fakes import FakeTransport, make_engine

WORKSPACE = Path("/home/user/project")


def _composition(*names: str):
    return get_agents(list(names) or ["claude"], providers={}, include_bundled=False)


class TestBuildSessionImages:
    """Which image is built, whether it is forced, and how failure is reported."""

    def _manager(self) -> MagicMock:
        manager = MagicMock()
        manager.ensure_default_image.return_value = "paude:latest"
        manager.ensure_custom_image.return_value = "paude-custom:latest"
        manager.ensure_proxy_image.return_value = "paude-proxy:latest"
        return manager

    def _build(self, manager: MagicMock, **kwargs: object) -> SessionImages:
        with patch("paude.container.ImageManager", return_value=manager) as cls:
            images = build_session_images(
                engine=make_engine(),
                composition=_composition(),
                config=kwargs.pop("config", None),  # type: ignore[arg-type]
                workspace=WORKSPACE,
                force_rebuild=bool(kwargs.pop("force_rebuild", False)),
                platform=kwargs.pop("platform", None),  # type: ignore[arg-type]
            )
        self.manager_cls = cls
        return images

    def test_plain_config_builds_the_default_image(self) -> None:
        manager = self._manager()

        images = self._build(manager, config=PaudeConfig())

        assert images == SessionImages(agent="paude:latest", proxy="paude-proxy:latest")
        manager.ensure_custom_image.assert_not_called()

    def test_customized_config_builds_a_custom_image(self) -> None:
        manager = self._manager()
        config = PaudeConfig(packages=["ripgrep"])

        images = self._build(manager, config=config)

        assert images.agent == "paude-custom:latest"
        manager.ensure_custom_image.assert_called_once_with(
            config, force_rebuild=False, workspace=WORKSPACE
        )

    def test_force_rebuild_reaches_both_builds(self) -> None:
        manager = self._manager()

        self._build(manager, force_rebuild=True)

        manager.ensure_default_image.assert_called_once_with(force_rebuild=True)
        manager.ensure_proxy_image.assert_called_once_with(force_rebuild=True)

    def test_platform_reaches_the_image_manager(self) -> None:
        manager = self._manager()

        self._build(manager, platform="linux/amd64")

        assert self.manager_cls.call_args.kwargs["platform"] == "linux/amd64"

    def test_agent_build_failure_names_its_stage(self) -> None:
        manager = self._manager()
        cause = RuntimeError("boom")
        manager.ensure_default_image.side_effect = cause

        with pytest.raises(ImageBuildError) as exc:
            self._build(manager)

        assert exc.value.stage == "agent"
        assert exc.value.cause is cause
        # Reproduces upgrade's user-visible wording exactly.
        assert str(exc.value) == "building the agent image failed: boom"
        manager.ensure_proxy_image.assert_not_called()

    def test_proxy_build_failure_names_its_stage(self) -> None:
        manager = self._manager()
        manager.ensure_proxy_image.side_effect = RuntimeError("no proxy")

        with pytest.raises(ImageBuildError) as exc:
            self._build(manager)

        assert exc.value.stage == "proxy"
        assert str(exc.value) == "building the proxy image failed: no proxy"


class TestPrepareSessionMounts:
    """Local engines copy configs in; SSH remotes bind-mount synced copies."""

    def test_local_engine_skips_config_mounts_and_does_not_sync(self) -> None:
        with (
            patch("paude.mounts.build_mounts", return_value=["-v", "/a:/b"]) as build,
            patch("paude.transport.config_sync.sync_configs_to_remote") as sync,
        ):
            result = prepare_session_mounts(
                engine=make_engine(), composition=_composition()
            )

        assert result.mounts == ["-v", "/a:/b"]
        assert result.remote_config is None
        assert build.call_args.kwargs["include_config"] is False
        sync.assert_not_called()

    def test_ssh_remote_syncs_and_remaps(self) -> None:
        remote = RemoteConfigPaths(remote_base="/tmp/cfg", path_map={"/a": "/r/a"})
        engine = make_engine(transport=SshTransport("user@host"), is_remote=True)
        with (
            patch("paude.mounts.build_mounts", return_value=["-v", "/a:/b"]) as build,
            patch(
                "paude.transport.config_sync.sync_configs_to_remote",
                return_value=remote,
            ) as sync,
            patch(
                "paude.transport.config_sync.remap_mounts",
                return_value=["-v", "/r/a:/b"],
            ) as remap,
        ):
            result = prepare_session_mounts(engine=engine, composition=_composition())

        assert build.call_args.kwargs["include_config"] is True
        sync.assert_called_once()
        remap.assert_called_once()
        assert result.mounts == ["-v", "/r/a:/b"]
        assert result.remote_config is remote

    def test_remote_without_an_ssh_transport_does_not_sync(self) -> None:
        """is_remote is transport-derived; only SSH has anything to sync."""
        engine = make_engine(transport=FakeTransport(is_remote=True), is_remote=True)
        with (
            patch("paude.mounts.build_mounts", return_value=[]),
            patch("paude.transport.config_sync.sync_configs_to_remote") as sync,
        ):
            result = prepare_session_mounts(engine=engine, composition=_composition())

        assert result.remote_config is None
        sync.assert_not_called()


class TestSessionConfigFromSpec:
    """The spec plus this build's outputs become a SessionConfig."""

    def _config(self, spec: SessionSpec, **kwargs: object):
        composition = kwargs.pop("composition", None) or _composition()
        return session_config_from_spec(
            spec,
            name="s",
            workspace=WORKSPACE,
            composition=composition,  # type: ignore[arg-type]
            images=SessionImages(agent="img:1", proxy="proxy:1"),
            env={"FOO": "bar"},
            mounts=["-v", "/a:/b"],
            allowed_domains=["github.com"],
            otel_ports=[4317],
            **kwargs,  # type: ignore[arg-type]
        )

    def test_carries_the_spec_onto_the_config(self) -> None:
        spec = SessionSpec(
            agent="codex",
            provider="openai",
            credential_providers=["openai"],
            gpu="all",
            yolo=True,
            otel_endpoint="http://otel:4317",
        )

        config = self._config(spec, composition=_composition("codex"))

        assert config.agent == "codex"
        assert config.provider == "openai"
        assert config.credential_providers == ["openai"]
        assert config.gpu == "all"
        assert config.yolo is True
        assert config.otel_endpoint == "http://otel:4317"
        assert config.image == "img:1"
        assert config.proxy_image == "proxy:1"
        assert config.env == {"FOO": "bar"}
        assert config.mounts == ["-v", "/a:/b"]
        assert config.allowed_domains == ["github.com"]
        assert config.otel_ports == [4317]

    def test_agent_providers_come_from_the_composition(self) -> None:
        """Not from the spec: an upgrade may have just changed the agent set."""
        config = self._config(
            SessionSpec(), composition=_composition("claude", "codex")
        )

        assert [name for name, _p in config.agent_providers] == ["claude", "codex"]

    def test_credential_providers_fall_back_to_the_agents_own(self) -> None:
        config = self._config(SessionSpec(), composition=_composition("claude"))

        assert config.credential_providers == ["vertex"]

    def test_recorded_proxy_image_is_the_fallback(self) -> None:
        """A rebuild whose proxy build produced nothing keeps the old label."""
        config = session_config_from_spec(
            SessionSpec(proxy_image="recorded:1"),
            name="s",
            workspace=WORKSPACE,
            composition=_composition(),
            images=SessionImages(agent="img:1", proxy=None),
            env={},
            mounts=[],
            allowed_domains=[],
            otel_ports=[],
        )

        assert config.proxy_image == "recorded:1"

    def test_args_workdir_and_reuse_volume_default_off(self) -> None:
        config = self._config(SessionSpec())

        assert config.args == []
        assert config.workdir is None
        assert config.reuse_volume is False

    def test_create_passes_args_and_workdir(self) -> None:
        config = self._config(SessionSpec(), args=["--verbose"], workdir=str(WORKSPACE))

        assert config.args == ["--verbose"]
        assert config.workdir == str(WORKSPACE)
