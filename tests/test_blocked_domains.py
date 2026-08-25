"""Tests for the blocked-domains backend methods."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError
from tests.fakes import make_backend

# ---------------------------------------------------------------------------
# PodmanBackend: get_proxy_blocked_log
# ---------------------------------------------------------------------------


class TestPodmanGetProxyBlockedLog:
    """Tests for PodmanBackend.get_proxy_blocked_log method."""

    def test_returns_none_when_no_proxy(self) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )

        backend = make_backend(mock_runner)

        result = backend.get_proxy_blocked_log("my-session")
        assert result is None

    def test_raises_session_not_found(self) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.get_proxy_blocked_log("nonexistent")

    def test_raises_value_error_when_proxy_not_running(self) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(ValueError, match="not running"):
            backend.get_proxy_blocked_log("my-session")

    def test_returns_empty_string_when_log_file_missing(self) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.exec_in_container.return_value = MagicMock(
            returncode=1, stdout="", stderr="No such file"
        )

        backend = make_backend(mock_runner)

        result = backend.get_proxy_blocked_log("my-session")
        assert result == ""

    def test_returns_log_content(self) -> None:
        log_content = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.exec_in_container.return_value = MagicMock(
            returncode=0, stdout=log_content
        )

        backend = make_backend(mock_runner)

        result = backend.get_proxy_blocked_log("my-session")
        assert result == log_content
        mock_runner.exec_in_container.assert_called_once_with(
            "paude-proxy-my-session",
            ["cat", "/tmp/paude-proxy-blocked.log"],
            check=False,
        )
