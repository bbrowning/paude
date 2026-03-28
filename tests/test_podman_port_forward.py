"""Tests for Podman port-forward manager."""

from __future__ import annotations

import signal
import sys
from unittest.mock import MagicMock, patch

from paude.backends.podman.port_forward import PodmanPortForwardManager


def _make_engine(binary: str = "podman", is_remote: bool = False) -> MagicMock:
    """Create a mock ContainerEngine."""
    engine = MagicMock()
    engine.binary = binary
    engine.transport.is_remote = is_remote
    return engine


class TestPodmanPortForwardManagerStart:
    """Tests for PodmanPortForwardManager.start."""

    def test_no_ports_does_nothing(self, tmp_path) -> None:
        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        mgr.start("my-session", "paude-my-session", [])
        # Should not create any PID file
        pf = tmp_path / "my-session.pid"
        assert not pf.exists()

    @patch("paude.backends.podman.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_starts_port_forward(self, mock_pid_dir, mock_popen, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_proc = MagicMock()
        mock_proc.pid = 54321
        mock_popen.return_value = mock_proc

        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        mgr.start("my-session", "paude-my-session", [(18789, 18789)])

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]

        assert cmd[0] == sys.executable
        assert "-m" in cmd
        assert "paude.backends.podman.port_forward_proxy" in cmd
        assert "--parent-pid" in cmd
        assert "--forward" in cmd
        assert "18789" in cmd
        # The exec command is shell-quoted into a single string argument
        cmd_str = " ".join(cmd)
        assert "podman" in cmd_str
        assert "exec" in cmd_str
        assert "paude-my-session" in cmd_str
        assert "socat" in cmd_str
        assert "TCP:127.0.0.1:18789" in cmd_str

        pid_f = tmp_path / "my-session.pid"
        assert pid_f.exists()
        assert pid_f.read_text() == "54321"

    @patch("paude.backends.podman.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_starts_with_multiple_ports(
        self, mock_pid_dir, mock_popen, tmp_path
    ) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_proc = MagicMock()
        mock_proc.pid = 54321
        mock_popen.return_value = mock_proc

        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        mgr.start("my-session", "paude-my-session", [(8080, 8080), (9090, 9090)])

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "--forward" in cmd_str
        assert "8080" in cmd_str
        assert "TCP:127.0.0.1:8080" in cmd_str
        assert "9090" in cmd_str
        assert "TCP:127.0.0.1:9090" in cmd_str

    @patch("paude.backends.port_forward_utils.is_process_running")
    @patch("paude.backends.podman.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_idempotent_when_already_running(
        self, mock_pid_dir, mock_popen, mock_running, tmp_path
    ) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("54321")

        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        mgr.start("my-session", "paude-my-session", [(18789, 18789)])

        mock_popen.assert_not_called()

    @patch("paude.backends.podman.port_forward.subprocess.Popen")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_replaces_stale_pid(self, mock_pid_dir, mock_popen, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path

        # Stale PID file (process not running)
        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("99999999")

        mock_proc = MagicMock()
        mock_proc.pid = 11111
        mock_popen.return_value = mock_proc

        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        mgr.start("my-session", "paude-my-session", [(18789, 18789)])

        mock_popen.assert_called_once()
        assert pid_f.read_text() == "11111"


class TestPodmanPortForwardManagerStop:
    """Tests for PodmanPortForwardManager.stop."""

    def test_stop_when_no_pid_file(self) -> None:
        engine = _make_engine()
        mgr = PodmanPortForwardManager(engine)
        # Should not raise
        mgr.stop("nonexistent-session")

    @patch("paude.backends.port_forward_utils.is_process_running")
    @patch("paude.backends.port_forward_utils.pid_dir")
    def test_stop_kills_process(self, mock_pid_dir, mock_running, tmp_path) -> None:
        mock_pid_dir.return_value = tmp_path
        mock_running.return_value = True

        pid_f = tmp_path / "my-session.pid"
        pid_f.write_text("54321")

        with patch("paude.backends.port_forward_utils.os.kill") as mock_kill:
            engine = _make_engine()
            mgr = PodmanPortForwardManager(engine)
            mgr.stop("my-session")

            mock_kill.assert_called_once_with(54321, signal.SIGTERM)

        assert not pid_f.exists()


class TestBuildExecCmd:
    """Tests for PodmanPortForwardManager._build_exec_cmd."""

    def test_local_transport(self) -> None:
        engine = _make_engine(binary="podman")
        mgr = PodmanPortForwardManager(engine)
        cmd = mgr._build_exec_cmd("paude-test", 18789)

        assert cmd == [
            "podman",
            "exec",
            "-i",
            "paude-test",
            "socat",
            "STDIO",
            "TCP:127.0.0.1:18789",
        ]

    def test_docker_binary(self) -> None:
        engine = _make_engine(binary="docker")
        mgr = PodmanPortForwardManager(engine)
        cmd = mgr._build_exec_cmd("paude-test", 8080)

        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "TCP:127.0.0.1:8080" in cmd

    def test_ssh_transport(self) -> None:
        from paude.transport.ssh import SshTransport

        ssh = SshTransport("user@remote", key="/tmp/key", port=2222)
        engine = MagicMock()
        engine.binary = "podman"
        engine.transport = ssh

        mgr = PodmanPortForwardManager(engine)
        cmd = mgr._build_exec_cmd("paude-test", 18789)

        assert cmd[0] == "ssh"
        assert "user@remote" in cmd
        assert "-i" in cmd
        assert "/tmp/key" in cmd
        assert "-p" in cmd
        assert "2222" in cmd
        assert "--" in cmd
        # The podman exec command is joined into a single shell string after --
        joined = cmd[cmd.index("--") + 1]
        assert "podman exec -i paude-test socat STDIO TCP:127.0.0.1:18789" in joined
