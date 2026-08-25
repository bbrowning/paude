"""Regression tests for the Podman/Docker-only backend model."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from paude.backends.podman.backend import PodmanBackend
from paude.cli import app
from paude.cli.app import BackendType
from paude.cli.helpers import _get_backend_instance
from paude.config.resolver import resolve_create_options
from paude.config.user_config import UserDefaults
from paude.registry import SessionRegistry
from paude.transport import LocalTransport, SshTransport


def _resolve_defaults(defaults: UserDefaults) -> None:
    resolve_create_options(
        cli_backend=None,
        cli_agent=None,
        cli_yolo=None,
        cli_git=None,
        cli_platform=None,
        cli_gpu=None,
        cli_allowed_domains=None,
        project_config=None,
        user_defaults=defaults,
    )


def test_only_container_engines_are_backend_types() -> None:
    assert set(BackendType) == {BackendType.podman, BackendType.docker}


def test_legacy_openshift_default_has_clear_error() -> None:
    with pytest.raises(ValueError, match="Supported backends are: podman, docker"):
        _resolve_defaults(UserDefaults(backend="openshift"))


def test_removed_backend_and_flags_are_rejected() -> None:
    runner = CliRunner()
    backend_result = runner.invoke(app, ["create", "--backend", "openshift"])
    flag_result = runner.invoke(app, ["create", "--openshift-context", "old"])

    assert backend_result.exit_code != 0
    assert flag_result.exit_code != 0


def test_legacy_registry_entry_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "old": {
                        "name": "old",
                        "backend_type": "openshift",
                        "workspace": "/tmp/old",
                        "agent": "claude",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    "current": {
                        "name": "current",
                        "backend_type": "docker",
                        "workspace": "/tmp/current",
                        "agent": "codex",
                        "created_at": "2026-01-01T00:00:00Z",
                        "openshift_context": None,
                        "openshift_namespace": None,
                    },
                }
            }
        )
    )

    with caplog.at_level(logging.WARNING):
        entries = SessionRegistry(path).load()

    assert set(entries) == {"current"}
    assert entries["current"].engine == "podman"
    assert "Ignoring legacy OpenShift session 'old'" in caplog.text


def test_force_delete_removes_legacy_openshift_session(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(
        json.dumps(
            {
                "sessions": {
                    "old-ocp": {
                        "name": "old-ocp",
                        "backend_type": "openshift",
                        "workspace": "/tmp/old",
                        "agent": "claude",
                        "created_at": "2026-01-01T00:00:00Z",
                        "openshift_context": "ctx",
                        "openshift_namespace": "ns",
                    },
                    "keep": {
                        "name": "keep",
                        "backend_type": "podman",
                        "workspace": "/tmp/keep",
                        "agent": "claude",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                }
            }
        )
    )
    reg = SessionRegistry(path)

    assert reg.unregister("old-ocp") is True
    data = json.loads(path.read_text())
    assert "old-ocp" not in data["sessions"]
    assert "keep" in data["sessions"]


def test_unregister_returns_false_for_missing(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({"sessions": {}}))
    reg = SessionRegistry(path)

    assert reg.unregister("nonexistent") is False


@pytest.mark.parametrize("backend", [BackendType.podman, BackendType.docker])
def test_local_engine_backend(backend: BackendType) -> None:
    instance = _get_backend_instance(backend)

    assert isinstance(instance, PodmanBackend)
    assert instance.engine.binary == backend.value
    assert isinstance(instance.engine.transport, LocalTransport)


@pytest.mark.parametrize("backend", [BackendType.podman, BackendType.docker])
def test_ssh_engine_backend(backend: BackendType) -> None:
    instance = _get_backend_instance(
        backend, ssh_host="user@example.test:2222", ssh_key="/tmp/test-key"
    )

    assert isinstance(instance, PodmanBackend)
    assert instance.engine.binary == backend.value
    transport = instance.engine.transport
    assert isinstance(transport, SshTransport)
    assert transport.host == "user@example.test"
    assert transport.port == 2222
