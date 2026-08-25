"""Pytest fixtures for paude tests."""

import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_INTEGRATION_DIR = Path(__file__).parent / "integration"


def _is_integration(request) -> bool:
    """Whether this test lives under tests/integration/.

    Matches the directory rather than the string "integration" anywhere in the
    path: tests/test_integration.py is a unit test that a substring check
    wrongly excluded from config isolation.
    """
    return _INTEGRATION_DIR in Path(str(request.fspath)).parents


@pytest.fixture(autouse=True)
def _isolate_config(request, tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME and cwd to temp dirs for every test.

    Prevents tests from reading or writing the real
    ~/.config/paude/ (sessions registry, user defaults, etc.)
    and from picking up workspace config files (paude.json,
    paude.json via detect_config().

    Skipped for integration tests which need real container engine
    config (e.g. podman network definitions).
    """
    if not _is_integration(request):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
        monkeypatch.chdir(tmp_path)


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


class _InstantSleep:
    """Stand-in for the ``time`` module whose ``sleep`` returns immediately.

    Every other attribute delegates to the real module, so a poll loop that
    also reads a clock still behaves normally.
    """

    def __init__(self, real: ModuleType) -> None:
        self._real = real

    def sleep(self, seconds: float) -> None:
        """Return immediately instead of blocking."""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


# Modules whose retry/poll loops call time.sleep(). Left real, these dominate
# the unit suite's wall clock: tests drive them with mocked runners that never
# satisfy the loop's exit condition, so each one burns its full poll budget.
_POLL_SLEEP_MODULES = (
    "paude.backends.podman.ca_cert",
    "paude.backends.podman.proxy",
    "paude.container.proxy_runner",
)


@pytest.fixture(autouse=True)
def _no_poll_sleep(request, monkeypatch):
    """Make retry/poll sleeps instant for unit tests.

    Safe because every one of these loops bounds itself by counting -- an
    iteration counter (proxy._get_proxy_ip) or an accumulator incremented by
    the interval constant (ca_cert), never by wall clock -- so removing the
    delay preserves timeout semantics exactly, including the warning paths.

    Skipped for integration tests, which drive a real container engine and
    need real waits.
    """
    if _is_integration(request):
        return
    for name in _POLL_SLEEP_MODULES:
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "time", _InstantSleep(module.time))


@pytest.fixture
def backend():
    """A ``PodmanBackend`` with every collaborator replaced by a double."""
    from tests.fakes import make_backend

    return make_backend()
