"""Tests for the blocked-domains backend methods."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from paude.backends.podman import PodmanBackend
from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError

# ---------------------------------------------------------------------------
# PodmanBackend: get_proxy_blocked_log
# ---------------------------------------------------------------------------


class TestPodmanGetProxyBlockedLog:
    """Tests for PodmanBackend.get_proxy_blocked_log method."""

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_none_when_no_proxy(self, mock_runner_class: MagicMock) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_proxy_blocked_log("my-session")
        assert result is None

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_raises_session_not_found(self, mock_runner_class: MagicMock) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        with pytest.raises(PodmanSessionNotFoundError):
            backend.get_proxy_blocked_log("nonexistent")

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_raises_value_error_when_proxy_not_running(
        self, mock_runner_class: MagicMock
    ) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        with pytest.raises(ValueError, match="not running"):
            backend.get_proxy_blocked_log("my-session")

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_empty_string_when_log_file_missing(
        self, mock_runner_class: MagicMock
    ) -> None:
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.exec_in_container.return_value = MagicMock(
            returncode=1, stdout="", stderr="No such file"
        )
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_proxy_blocked_log("my-session")
        assert result == ""

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_log_content(self, mock_runner_class: MagicMock) -> None:
        log_content = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.exec_in_container.return_value = MagicMock(
            returncode=0, stdout=log_content
        )
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_proxy_blocked_log("my-session")
        assert result == log_content
        mock_runner.exec_in_container.assert_called_once_with(
            "paude-proxy-my-session",
            ["cat", "/tmp/paude-proxy-blocked.log"],
            check=False,
        )
