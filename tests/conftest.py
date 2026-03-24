"""Pytest fixtures for paude tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(request, tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME to a temp dir for every test.

    Prevents tests from reading or writing the real
    ~/.config/paude/ (sessions registry, user defaults, etc.).

    Skipped for integration tests which need real container engine
    config (e.g. podman network definitions).
    """
    if "integration" not in str(request.fspath):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace
