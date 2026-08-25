"""Characterization tests for the Podman/Docker create pipeline.

``create_podman_session`` had no direct coverage (13%, its whole body uncovered)
while its near-twin ``_upgrade_podman`` sits in a well-tested module. These tests
pin what create *does* -- the ``SessionConfig`` it assembles, its two
image-failure paths, its rollback, and its post-create calls -- so the
session-rebuild consolidation can shrink the function without changing it.

Patch targets are deliberate. Collaborators that ``create_podman_session``
imports *inside* the function (``ImageManager``, ``build_mounts``, the
``config_sync`` helpers) are patched at their defining module, so these tests
keep biting once that code moves into the shared rebuild helpers. Collaborators
bound at module import (``PodmanBackend``, ``_finalize_session_create``,
``_run_setup_command``) are patched on ``paude.cli.create_podman``, where they
stay.

Deliberately *not* asserted: the relative order of the config sync and the proxy
image build. The consolidation unifies that ordering on purpose.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

from paude.backends import SessionConfig, SessionExistsError
from paude.backends.base import Session
from paude.cli.create_podman import create_podman_session
from paude.config.models import PaudeConfig
from paude.transport.config_sync import RemoteConfigPaths
from paude.transport.ssh import SshTransport

WORKSPACE = Path("/home/user/project")
REMOTE_BASE = "/tmp/paude-config-abc123"

# The provider the agent registry resolves for a bare `claude` / `codex`.
CLAUDE_PROVIDER = "vertex"
CODEX_PROVIDER = "chatgpt"


@dataclass
class _Pipeline:
    """Handles on every collaborator ``create_podman_session`` drives."""

    image_manager_cls: MagicMock
    image_manager: MagicMock
    build_mounts: MagicMock
    backend: MagicMock
    session: Session
    finalize: MagicMock
    run_setup: MagicMock
    sync: MagicMock
    remap: MagicMock
    cleanup_remote: MagicMock

    @property
    def config(self) -> SessionConfig:
        """The ``SessionConfig`` handed to ``create_session``."""
        created: SessionConfig = self.backend.create_session.call_args[0][0]
        return created


@contextmanager
def _pipeline(*, mounts: list[str] | None = None) -> Iterator[_Pipeline]:
    """Run ``create_podman_session`` against fully substituted collaborators."""
    session = Session(
        name="test-session",
        status="running",
        workspace=WORKSPACE,
        created_at="2026-01-01T00:00:00+00:00",
        backend_type="podman",
    )
    image_manager = MagicMock()
    image_manager.ensure_default_image.return_value = "paude:latest"
    image_manager.ensure_custom_image.return_value = "paude-custom:latest"
    image_manager.ensure_proxy_image.return_value = "paude-proxy:latest"

    backend = MagicMock()
    backend.create_session.return_value = session

    remote = RemoteConfigPaths(
        remote_base=REMOTE_BASE, path_map={"/local/cfg": "/remote/cfg"}
    )

    with ExitStack() as stack:
        enter = stack.enter_context
        image_manager_cls = enter(
            patch("paude.container.ImageManager", return_value=image_manager)
        )
        build_mounts = enter(
            patch("paude.mounts.build_mounts", return_value=list(mounts or []))
        )
        enter(patch("paude.cli.create_podman.PodmanBackend", return_value=backend))
        finalize = enter(patch("paude.cli.create_podman._finalize_session_create"))
        run_setup = enter(patch("paude.cli.create_podman._run_setup_command"))
        sync = enter(
            patch(
                "paude.transport.config_sync.sync_configs_to_remote",
                return_value=remote,
            )
        )
        remap = enter(
            patch(
                "paude.transport.config_sync.remap_mounts",
                return_value=["-v", "/remote/cfg:/home/paude/.cfg:ro"],
            )
        )
        cleanup_remote = enter(
            patch("paude.transport.config_sync.cleanup_remote_configs")
        )
        yield _Pipeline(
            image_manager_cls=image_manager_cls,
            image_manager=image_manager,
            build_mounts=build_mounts,
            backend=backend,
            session=session,
            finalize=finalize,
            run_setup=run_setup,
            sync=sync,
            remap=remap,
            cleanup_remote=cleanup_remote,
        )


def _create(**overrides: Any) -> None:
    """Call ``create_podman_session`` with a plausible default argument set."""
    kwargs: dict[str, Any] = {
        "name": "test-session",
        "workspace": WORKSPACE,
        "config": None,
        "env": {"PAUDE_AGENT": "claude"},
        "expanded_domains": ["github.com", "pypi.org"],
        "parsed_args": ["--verbose"],
        "yolo": False,
        "git": False,
        "rebuild": False,
        "platform": None,
    }
    kwargs.update(overrides)
    create_podman_session(**kwargs)


def _remote_kwargs() -> dict[str, Any]:
    """Arguments that put the pipeline on the SSH-remote path."""
    return {
        "transport": SshTransport("user@remote"),
        "ssh_host": "user@remote",
        "ssh_key": "~/.ssh/id_ed25519",
    }


class TestSessionConfig:
    """The SessionConfig create assembles from its arguments."""

    def test_maps_every_argument_onto_the_session_config(self) -> None:
        with _pipeline(mounts=["-v", "/host:/pvc"]) as p:
            _create(
                env={"FOO": "bar"},
                yolo=True,
                gpu="all",
                otel_ports=[4317],
                otel_endpoint="http://otel:4317",
            )

        config = p.config
        assert config.name == "test-session"
        assert config.workspace == WORKSPACE
        assert config.image == "paude:latest"
        assert config.env == {"FOO": "bar"}
        assert config.mounts == ["-v", "/host:/pvc"]
        assert config.args == ["--verbose"]
        assert config.workdir == str(WORKSPACE)
        assert config.allowed_domains == ["github.com", "pypi.org"]
        assert config.yolo is True
        assert config.proxy_image == "paude-proxy:latest"
        assert config.agent == "claude"
        assert config.provider is None
        assert config.agent_providers == [("claude", CLAUDE_PROVIDER)]
        assert config.gpu == "all"
        assert config.ports == []
        assert config.otel_ports == [4317]
        assert config.otel_endpoint == "http://otel:4317"
        # Create builds a fresh volume; only upgrade/restore reuse one.
        assert config.reuse_volume is False

    def test_agent_providers_are_resolved_through_the_registry(self) -> None:
        with _pipeline() as p:
            _create(agent_providers=[("claude", ""), ("codex", "")])

        assert p.config.agent_providers == [
            ("claude", CLAUDE_PROVIDER),
            ("codex", CODEX_PROVIDER),
        ]

    def test_credential_providers_fall_back_to_the_resolved_providers(self) -> None:
        with _pipeline() as p:
            _create(
                agent_providers=[("claude", ""), ("codex", "")],
                credential_providers=None,
            )

        assert p.config.credential_providers == [CLAUDE_PROVIDER, CODEX_PROVIDER]

    def test_explicit_credential_providers_are_kept_verbatim(self) -> None:
        with _pipeline() as p:
            _create(credential_providers=["vertex", "openai"])

        assert p.config.credential_providers == ["vertex", "openai"]

    def test_session_is_started_without_attaching(self) -> None:
        with _pipeline() as p:
            _create()

        p.backend.start_session_no_attach.assert_called_once_with("test-session")

    def test_platform_reaches_the_image_manager(self) -> None:
        with _pipeline() as p:
            _create(platform="linux/amd64")

        assert p.image_manager_cls.call_args.kwargs["platform"] == "linux/amd64"


class TestImageSelection:
    """What create forwards into the image build.

    Which image the build then selects belongs to
    tests/test_session_rebuild.py, which owns build_session_images.
    """

    def test_customized_config_builds_a_custom_image(self) -> None:
        config = PaudeConfig(packages=["ripgrep"])
        with _pipeline() as p:
            _create(config=config)

        p.image_manager.ensure_custom_image.assert_called_once_with(
            config, force_rebuild=False, workspace=WORKSPACE
        )
        p.image_manager.ensure_default_image.assert_not_called()
        assert p.config.image == "paude-custom:latest"

    def test_rebuild_forces_both_image_builds(self) -> None:
        with _pipeline() as p:
            _create(rebuild=True)

        p.image_manager.ensure_default_image.assert_called_once_with(force_rebuild=True)
        p.image_manager.ensure_proxy_image.assert_called_once_with(force_rebuild=True)


class TestMounts:
    """What create does with the mounts it gets back.

    Which mounts those are -- local vs SSH, sync and remap -- belongs to
    tests/test_session_rebuild.py, which owns prepare_session_mounts.
    """

    def test_ssh_remote_records_the_config_dir_in_the_registry(self) -> None:
        with _pipeline() as p:
            _create(**_remote_kwargs())

        kwargs = p.finalize.call_args.kwargs
        assert kwargs["remote_config_dir"] == REMOTE_BASE
        assert kwargs["ssh_host"] == "user@remote"
        assert kwargs["ssh_key"] == "~/.ssh/id_ed25519"

    def test_local_engine_records_no_config_dir(self) -> None:
        with _pipeline() as p:
            _create()

        assert p.finalize.call_args.kwargs["remote_config_dir"] is None


class TestFailures:
    """Every failure path aborts with exit code 1, and what it cleans up."""

    def test_agent_image_failure_aborts_before_create(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _pipeline() as p:
            p.image_manager.ensure_default_image.side_effect = RuntimeError("boom")
            with pytest.raises(typer.Exit) as exc:
                _create()

        assert exc.value.exit_code == 1
        assert "Error ensuring image: boom" in capsys.readouterr().err
        p.backend.create_session.assert_not_called()

    def test_proxy_image_failure_aborts_before_create(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _pipeline() as p:
            p.image_manager.ensure_proxy_image.side_effect = RuntimeError("no proxy")
            with pytest.raises(typer.Exit) as exc:
                _create()

        assert exc.value.exit_code == 1
        assert "Error ensuring proxy image: no proxy" in capsys.readouterr().err
        p.backend.create_session.assert_not_called()

    def test_existing_session_reports_without_deleting_it(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _pipeline() as p:
            p.backend.create_session.side_effect = SessionExistsError("already exists")
            with pytest.raises(typer.Exit) as exc:
                _create()

        assert exc.value.exit_code == 1
        assert "Error: already exists" in capsys.readouterr().err
        # Distinct from the generic path: an existing session is never deleted.
        p.backend.delete_session.assert_not_called()

    def test_start_failure_rolls_the_session_back(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _pipeline() as p:
            p.backend.start_session_no_attach.side_effect = RuntimeError("no start")
            with pytest.raises(typer.Exit) as exc:
                _create()

        assert exc.value.exit_code == 1
        assert "Error creating session: no start" in capsys.readouterr().err
        p.backend.delete_session.assert_called_once_with("test-session", confirm=True)

    def test_captured_stderr_is_surfaced(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """str(CalledProcessError) omits stderr, so create echoes it separately."""
        failure = subprocess.CalledProcessError(
            125, ["podman", "run"], stderr="no such image"
        )
        with _pipeline() as p:
            p.backend.create_session.side_effect = failure
            with pytest.raises(typer.Exit):
                _create()

        err = capsys.readouterr().err
        assert "Error creating session:" in err
        assert "no such image" in err

    def test_create_failure_does_not_roll_back(self) -> None:
        """A create_session failure leaves no rollback -- `session` is unbound.

        The cleanup calls ``delete_session(session.name, ...)``, which raises
        ``UnboundLocalError`` into the best-effort ``except Exception: pass``.
        Characterized, not endorsed; see KNOWN_ISSUES.
        """
        with _pipeline() as p:
            p.backend.create_session.side_effect = RuntimeError("no create")
            with pytest.raises(typer.Exit):
                _create()

        p.backend.delete_session.assert_not_called()

    def test_create_failure_cleans_up_synced_remote_configs(self) -> None:
        with _pipeline() as p:
            p.backend.create_session.side_effect = RuntimeError("no create")
            with pytest.raises(typer.Exit):
                _create(**_remote_kwargs())

        assert p.cleanup_remote.call_args.args[1] == REMOTE_BASE

    def test_local_failure_has_no_remote_configs_to_clean(self) -> None:
        with _pipeline() as p:
            p.backend.create_session.side_effect = RuntimeError("no create")
            with pytest.raises(typer.Exit):
                _create()

        p.cleanup_remote.assert_not_called()


class TestPostCreate:
    """Registration and the optional setup command."""

    def test_registers_the_session_with_its_domains(self) -> None:
        with _pipeline() as p:
            _create(yolo=True, git=True, no_clone_origin=True)

        kwargs = p.finalize.call_args.kwargs
        assert kwargs["session"] is p.session
        assert kwargs["expanded_domains"] == ["github.com", "pypi.org"]
        assert kwargs["yolo"] is True
        assert kwargs["git"] is True
        assert kwargs["no_clone_origin"] is True

    def test_setup_command_runs_after_create(self) -> None:
        config = PaudeConfig(setup_command="make install")
        with _pipeline() as p:
            _create(config=config)

        p.run_setup.assert_called_once_with(p.backend, "test-session", "make install")

    def test_no_setup_command_stays_quiet(self) -> None:
        with _pipeline() as p:
            _create(config=PaudeConfig())

        p.run_setup.assert_not_called()
