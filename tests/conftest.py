"""Pytest fixtures for paude tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(request, tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME and cwd to temp dirs for every test.

    Prevents tests from reading or writing the real
    ~/.config/paude/ (sessions registry, user defaults, etc.)
    and from picking up workspace config files (paude.json,
    .devcontainer.json) via detect_config().

    Skipped for integration tests which need real container engine
    config (e.g. podman network definitions).
    """
    if "integration" not in str(request.fspath):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        monkeypatch.chdir(tmp_path)


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
