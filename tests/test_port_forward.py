"""Tests for OpenShift port-forward manager."""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

from paude.backends.openshift.port_forward import (
    PortForwardManager,
    _is_process_running,
    _pid_file,
)


class TestPortForwardManagerStart:
    """Tests for PortForwardManager.start."""

    def test_no_ports_does_nothing(self, tmp_path: str) -> None:
        mgr = PortForwardManager("test-ns")
        mgr.start("my-session", "pod-0", [])
        # Should not create any PID file
        assert not _pid_file("my-session").exists()

    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.openshift.port_forward._pid_dir")
    def test_starts_port_forward(self, mock_pid_dir, mock_popen, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        mgr = PortForwardManager("test-ns")
        mgr.start("my-session", "pod-0", [(18789, 18789)])

        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "oc" in cmd
        assert "port-forward" in cmd
        assert "-n" in cmd
        assert "test-ns" in cmd
        assert "pod-0" in cmd
        assert "18789:18789" in cmd

        pid_file = tmp_path / "my-session.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == "12345"

    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.openshift.port_forward._pid_dir")
    def test_includes_context_when_set(
        self, mock_pid_dir, mock_popen, tmp_path
    ) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        mgr = PortForwardManager("test-ns", context="my-ctx")
        mgr.start("my-session", "pod-0", [(18789, 18789)])

        cmd = mock_popen.call_args[0][0]
        assert "--context" in cmd
        assert "my-ctx" in cmd

    @patch("paude.backends.openshift.port_forward._is_process_running")
    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.openshift.port_forward._pid_dir")
    def test_idempotent_when_already_running(
        self, mock_pid_dir, mock_popen, mock_running, tmp_path
    ) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        # Write a PID file as if port-forward is already running
        pid_file = tmp_path / "my-session.pid"
        pid_file.write_text("12345")

        mgr = PortForwardManager("test-ns")
        mgr.start("my-session", "pod-0", [(18789, 18789)])

        # Should not start a new process
        mock_popen.assert_not_called()


class TestPortForwardManagerStop:
    """Tests for PortForwardManager.stop."""

    def test_stop_when_no_pid_file(self, tmp_path) -> None:
        # Should not raise
        mgr = PortForwardManager("test-ns")
        mgr.stop("nonexistent-session")

    @patch("paude.backends.openshift.port_forward._is_process_running")
    @patch("paude.backends.openshift.port_forward._pid_dir")
    def test_stop_kills_process(self, mock_pid_dir, mock_running, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        pid_file = tmp_path / "my-session.pid"
        pid_file.write_text("12345")

        with patch("os.kill") as mock_kill:
            mgr = PortForwardManager("test-ns")
            mgr.stop("my-session")

            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

        assert not pid_file.exists()

    @patch("paude.backends.openshift.port_forward._pid_dir")
    def test_stop_cleans_up_stale_pid(self, mock_pid_dir, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path

        pid_file = tmp_path / "my-session.pid"
        pid_file.write_text("99999")

        # Process is not running, but PID file exists
        with patch("os.kill", side_effect=OSError):
            mgr = PortForwardManager("test-ns")
            mgr.stop("my-session")

        assert not pid_file.exists()


class TestIsProcessRunning:
    """Tests for _is_process_running helper."""

    def test_current_process_is_running(self) -> None:
        assert _is_process_running(os.getpid()) is True

    def test_nonexistent_process(self) -> None:
        # PID 99999999 should not exist
        assert _is_process_running(99999999) is False
