"""Tests for transport layer."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from paude.transport import LocalTransport, SshTransport, parse_ssh_host


class TestParseSSHHost:
    def test_hostname_only(self) -> None:
        assert parse_ssh_host("myhost") == ("myhost", None)

    def test_user_at_hostname(self) -> None:
        assert parse_ssh_host("user@myhost") == ("user@myhost", None)

    def test_hostname_with_port(self) -> None:
        assert parse_ssh_host("myhost:2222") == ("myhost", 2222)

    def test_user_at_hostname_with_port(self) -> None:
        assert parse_ssh_host("user@myhost:2222") == ("user@myhost", 2222)

    def test_invalid_port_treated_as_host(self) -> None:
        assert parse_ssh_host("myhost:notaport") == ("myhost:notaport", None)


class TestLocalTransport:
    @patch("paude.transport.local.subprocess.run")
    def test_run_delegates_to_subprocess(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
        )
        transport = LocalTransport()
        result = transport.run(["echo", "hi"])
        mock_run.assert_called_once_with(
            ["echo", "hi"],
            check=True,
            capture_output=True,
            text=True,
            input=None,
            timeout=None,
        )
        assert result.stdout == "hi\n"

    @patch("paude.transport.local.subprocess.run")
    def test_run_interactive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=["bash"], returncode=0)
        transport = LocalTransport()
        rc = transport.run_interactive(["bash"])
        mock_run.assert_called_once_with(["bash"])
        assert rc == 0

    def test_is_remote(self) -> None:
        assert LocalTransport().is_remote is False

    def test_host_label(self) -> None:
        assert LocalTransport().host_label == "local"


class TestSshTransport:
    def test_ssh_base_minimal(self) -> None:
        transport = SshTransport("user@host")
        base = transport.ssh_base()
        assert base == [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "user@host",
        ]

    def test_ssh_base_with_key(self) -> None:
        transport = SshTransport("user@host", key="/path/to/key")
        base = transport.ssh_base()
        assert "-i" in base
        assert "/path/to/key" in base

    def test_ssh_base_with_port(self) -> None:
        transport = SshTransport("user@host", port=2222)
        base = transport.ssh_base()
        assert "-p" in base
        assert "2222" in base

    def test_ssh_base_with_key_and_port(self) -> None:
        transport = SshTransport("user@host", key="/key", port=2222)
        base = transport.ssh_base()
        assert "-i" in base
        assert "/key" in base
        assert "-p" in base
        assert "2222" in base

    @patch("paude.transport.ssh.subprocess.run")
    def test_run_prepends_ssh(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        transport = SshTransport("user@host")
        transport.run(["docker", "ps"])
        args = mock_run.call_args[0][0]
        # Should start with ssh command and end with -- 'shell-quoted cmd'
        assert args[0] == "ssh"
        assert "user@host" in args
        assert args[-2] == "--"
        assert args[-1] == "docker ps"

    @patch("paude.transport.ssh.subprocess.run")
    def test_run_quotes_special_chars(self, mock_run: MagicMock) -> None:
        """Ensure args with spaces/pipes are shell-quoted for the remote shell."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        transport = SshTransport("user@host")
        transport.run(
            [
                "docker",
                "create",
                "-e",
                "INSTALL=curl -fsSL https://example.com/install.sh | bash",
            ]
        )
        args = mock_run.call_args[0][0]
        cmd_string = args[-1]
        # The whole arg with spaces/pipes must be quoted in the single string
        assert (
            "'INSTALL=curl -fsSL https://example.com/install.sh | bash'" in cmd_string
        )

    @patch("paude.transport.ssh.subprocess.run")
    def test_run_interactive_uses_tty(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        transport = SshTransport("user@host")
        transport.run_interactive(["docker", "exec", "-it", "ctr", "bash"])
        args = mock_run.call_args[0][0]
        assert "-t" in args
        assert "--" in args
        # Command should be shell-quoted as a single string
        assert args[-1] == "docker exec -it ctr bash"

    def test_is_remote(self) -> None:
        assert SshTransport("user@host").is_remote is True

    def test_host_label(self) -> None:
        assert SshTransport("user@host").host_label == "user@host"

    def test_host_property(self) -> None:
        t = SshTransport("user@host", key="/k", port=22)
        assert t.host == "user@host"
        assert t.key == "/k"
        assert t.port == 22

    @patch("paude.transport.ssh.subprocess.run")
    def test_validate_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        transport = SshTransport("user@host")
        transport.validate()  # Should not raise

    @patch("paude.transport.ssh.subprocess.run")
    def test_validate_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
        transport = SshTransport("user@host")
        with pytest.raises(RuntimeError, match="SSH connection.*failed"):
            transport.validate()

    @patch("paude.transport.ssh.subprocess.run")
    def test_validate_engine_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Docker version 24.0\n", stderr=""
        )
        transport = SshTransport("user@host")
        transport.validate_engine("docker")  # Should not raise

    @patch("paude.transport.ssh.subprocess.run")
    def test_validate_engine_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=127, stdout="", stderr="not found"
        )
        transport = SshTransport("user@host")
        with pytest.raises(RuntimeError, match="'docker' not found"):
            transport.validate_engine("docker")
