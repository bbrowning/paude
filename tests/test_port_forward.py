"""Tests for port-forward manager and utilities."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.openshift.port_forward import PortForwardManager
from paude.backends.openshift.session_connection import (
    SessionConnector,
    _format_exit_reason,
    _monitor_port_forward,
    _show_port_forward_diagnostics,
)
from paude.backends.port_forward_utils import is_process_running, log_file, pid_file


class TestPortForwardManagerStart:
    """Tests for PortForwardManager.start."""

    def test_no_ports_does_nothing(self, tmp_path: str) -> None:
        mgr = PortForwardManager("test-ns")
        result = mgr.start("my-session", "pod-0", [])
        assert result is None
        assert not pid_file("my-session").exists()

    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_starts_port_forward(self, mock_pid_dir, mock_popen, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        mgr = PortForwardManager("test-ns")
        result = mgr.start("my-session", "pod-0", [(18789, 18789)])

        assert result is mock_proc
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert "oc" in cmd
        assert "port-forward" in cmd
        assert "-n" in cmd
        assert "test-ns" in cmd
        assert "pod-0" in cmd
        assert "18789:18789" in cmd

        # stdout/stderr should be file handles, not DEVNULL
        assert call_args[1]["stdout"] is not subprocess.DEVNULL
        assert call_args[1]["stderr"] is not subprocess.DEVNULL
        assert call_args[1]["stdin"] is subprocess.DEVNULL

        pid_f = tmp_path / "my-session.pid"
        assert pid_f.exists()
        assert pid_f.read_text() == "12345"

        # Log file should have been created
        log_f = tmp_path / "my-session.log"
        assert log_f.exists()

    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
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

    @patch("paude.backends.port_forward_utils.is_process_running")
    @patch("paude.backends.openshift.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_idempotent_when_already_running(
        self, mock_pid_dir, mock_popen, mock_running, tmp_path
    ) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        # Write a PID file as if port-forward is already running
        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("12345")

        mgr = PortForwardManager("test-ns")
        result = mgr.start("my-session", "pod-0", [(18789, 18789)])

        assert result is None
        # Should not start a new process
        mock_popen.assert_not_called()


class TestPortForwardManagerStop:
    """Tests for PortForwardManager.stop."""

    def test_stop_when_no_pid_file(self, tmp_path) -> None:
        # Should not raise
        mgr = PortForwardManager("test-ns")
        mgr.stop("nonexistent-session")

    @patch("paude.backends.port_forward_utils.is_process_running")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_stop_kills_process(self, mock_pid_dir, mock_running, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("12345")

        with patch("paude.backends.port_forward_utils.os.kill") as mock_kill:
            mgr = PortForwardManager("test-ns")
            mgr.stop("my-session")

            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

        assert not pid_f.exists()

    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_stop_cleans_up_stale_pid(self, mock_pid_dir, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path

        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("99999")

        # Process is not running, but PID file exists
        with patch("paude.backends.port_forward_utils.os.kill", side_effect=OSError):
            mgr = PortForwardManager("test-ns")
            mgr.stop("my-session")

        assert not pid_f.exists()

    @patch("paude.backends.port_forward_utils.is_process_running")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_stop_cleans_log_file(self, mock_pid_dir, mock_running, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("12345")
        log_f = tmp_path / "my-session.log"
        log_f.write_text("some log output\n")

        with patch("paude.backends.port_forward_utils.os.kill"):
            mgr = PortForwardManager("test-ns")
            mgr.stop("my-session")

        assert not pid_f.exists()
        assert not log_f.exists()


class TestIsProcessRunning:
    """Tests for is_process_running helper."""

    def test_current_process_is_running(self) -> None:
        assert is_process_running(os.getpid()) is True

    def test_nonexistent_process(self) -> None:
        # PID 99999999 should not exist
        assert is_process_running(99999999) is False

    def test_zombie_child_detected_as_not_running(self) -> None:
        """A zombie (defunct) child process should be detected as not running."""
        proc = subprocess.Popen(["true"])  # noqa: S603, S607
        pid = proc.pid
        # Let the child exit without reaping it (no proc.wait())
        time.sleep(0.2)
        # is_process_running should reap the zombie and return False
        assert is_process_running(pid) is False


class TestLogFile:
    """Tests for log_file helper."""

    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_log_file_path(self, mock_pid_dir, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        assert log_file("my-session") == tmp_path / "my-session.log"


class TestFormatExitReason:
    """Tests for _format_exit_reason."""

    def test_signal_death(self) -> None:
        assert "killed by SIGKILL" in _format_exit_reason(-9)

    def test_signal_death_sigterm(self) -> None:
        assert "killed by SIGTERM" in _format_exit_reason(-15)

    def test_exit_code(self) -> None:
        assert _format_exit_reason(1) == "exited with code 1"

    def test_exit_code_zero(self) -> None:
        assert _format_exit_reason(0) == "exited with code 0"


class TestMonitorPortForward:
    """Tests for _monitor_port_forward."""

    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_detects_process_death(self, mock_pid_dir, tmp_path) -> None:
        """Monitor should detect when the process dies and write a warning."""
        mock_pid_dir.return_value = tmp_path
        # Use a real short-lived process
        proc = subprocess.Popen(["sleep", "0.1"])  # noqa: S603, S607
        stop_event = threading.Event()

        stderr_capture = StringIO()
        with patch(
            "paude.backends.openshift.session_connection.sys.stderr", stderr_capture
        ):
            _monitor_port_forward(proc, "test-session", stop_event, check_interval=0.3)

        output = stderr_capture.getvalue()
        assert "WARNING" in output
        assert "test-session" in output

    def test_exits_on_stop_event(self) -> None:
        """Monitor should exit cleanly when stop_event is set."""
        proc = subprocess.Popen(["sleep", "60"])  # noqa: S603, S607
        stop_event = threading.Event()

        thread = threading.Thread(
            target=_monitor_port_forward,
            args=(proc, "test-session", stop_event, 0.3),
        )
        thread.start()

        # Set stop event after a brief delay
        time.sleep(0.1)
        stop_event.set()
        thread.join(timeout=2.0)

        assert not thread.is_alive()
        proc.terminate()
        proc.wait()


class TestShowPortForwardDiagnostics:
    """Tests for SessionConnector._show_port_forward_diagnostics."""

    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_shows_log_tail_on_death(self, mock_pid_dir, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path

        log_f = tmp_path / "test-session.log"
        log_f.write_text("line 1\nline 2\nerror: connection refused\n")

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1

        stderr_capture = StringIO()
        with patch(
            "paude.backends.openshift.session_connection.sys.stderr", stderr_capture
        ):
            _show_port_forward_diagnostics("test-session", mock_proc)

        output = stderr_capture.getvalue()
        assert "exited with code 1" in output
        assert "error: connection refused" in output
        assert "paude connect" in output

    def test_no_output_when_still_running(self) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running

        stderr_capture = StringIO()
        with patch(
            "paude.backends.openshift.session_connection.sys.stderr", stderr_capture
        ):
            _show_port_forward_diagnostics("test-session", mock_proc)

        assert stderr_capture.getvalue() == ""

    def test_no_output_when_no_proc(self) -> None:
        stderr_capture = StringIO()
        with patch(
            "paude.backends.openshift.session_connection.sys.stderr", stderr_capture
        ):
            _show_port_forward_diagnostics("test-session", None)

        assert stderr_capture.getvalue() == ""

    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_shows_signal_death(self, mock_pid_dir, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path

        mock_proc = MagicMock()
        mock_proc.poll.return_value = -9  # SIGKILL

        stderr_capture = StringIO()
        with patch(
            "paude.backends.openshift.session_connection.sys.stderr", stderr_capture
        ):
            _show_port_forward_diagnostics("test-session", mock_proc)

        output = stderr_capture.getvalue()
        assert "killed by SIGKILL" in output


class TestSessionConnectorCleanup:
    """Tests for connect_session port-forward cleanup."""

    @patch("paude.backends.openshift.session_connection._show_port_forward_diagnostics")
    @patch.object(SessionConnector, "_stop_port_forward")
    @patch.object(SessionConnector, "_attach_to_pod", return_value=0)
    @patch.object(SessionConnector, "_start_port_forward", return_value=(None, []))
    @patch.object(SessionConnector, "_sync_for_connect")
    @patch.object(SessionConnector, "_verify_pod_running", return_value=("pod-0", "ns"))
    def test_connect_stops_port_forward_on_success(
        self,
        mock_verify: MagicMock,  # noqa: ARG002
        mock_sync: MagicMock,  # noqa: ARG002
        mock_start_pf: MagicMock,  # noqa: ARG002
        mock_attach: MagicMock,  # noqa: ARG002
        mock_stop_pf: MagicMock,
        mock_diag: MagicMock,  # noqa: ARG002
    ) -> None:
        connector = SessionConnector(
            MagicMock(), "ns", MagicMock(), MagicMock(), MagicMock()
        )
        connector.connect_session("test-session")
        mock_stop_pf.assert_called_once_with("test-session")

    @patch("paude.backends.openshift.session_connection._show_port_forward_diagnostics")
    @patch.object(SessionConnector, "_stop_port_forward")
    @patch.object(SessionConnector, "_attach_to_pod", side_effect=RuntimeError("boom"))
    @patch.object(SessionConnector, "_start_port_forward", return_value=(None, []))
    @patch.object(SessionConnector, "_sync_for_connect")
    @patch.object(SessionConnector, "_verify_pod_running", return_value=("pod-0", "ns"))
    def test_connect_stops_port_forward_on_error(
        self,
        mock_verify: MagicMock,  # noqa: ARG002
        mock_sync: MagicMock,  # noqa: ARG002
        mock_start_pf: MagicMock,  # noqa: ARG002
        mock_attach: MagicMock,  # noqa: ARG002
        mock_stop_pf: MagicMock,
        mock_diag: MagicMock,  # noqa: ARG002
    ) -> None:
        connector = SessionConnector(
            MagicMock(), "ns", MagicMock(), MagicMock(), MagicMock()
        )
        with pytest.raises(RuntimeError):
            connector.connect_session("test-session")
        mock_stop_pf.assert_called_once_with("test-session")
