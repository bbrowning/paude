"""Tests for platform-specific code."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from paude.platform import get_podman_machine_dns, is_macos


class TestIsMacos:
    """Tests for is_macos."""

    def test_returns_boolean(self):
        """is_macos returns boolean."""
        result = is_macos()
        assert isinstance(result, bool)

    @patch("paude.platform.platform.system")
    def test_returns_true_on_darwin(self, mock_system):
        """is_macos returns True on Darwin."""
        mock_system.return_value = "Darwin"
        assert is_macos() is True

    @patch("paude.platform.platform.system")
    def test_returns_false_on_linux(self, mock_system):
        """is_macos returns False on Linux."""
        mock_system.return_value = "Linux"
        assert is_macos() is False


class TestGetPodmanMachineDns:
    """Tests for get_podman_machine_dns."""

    @patch("paude.platform.is_macos")
    def test_returns_none_on_linux(self, mock_is_macos):
        """get_podman_machine_dns returns None when not on macOS."""
        mock_is_macos.return_value = False
        result = get_podman_machine_dns()
        assert result is None

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_parses_nameserver_ip_from_resolv_conf(self, mock_is_macos, mock_run):
        """get_podman_machine_dns parses nameserver IP from resolv.conf."""
        mock_is_macos.return_value = True
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "machine", "ssh", "grep", "nameserver", "/etc/resolv.conf"],
            returncode=0,
            stdout="nameserver 192.168.127.1\n",
            stderr="",
        )
        result = get_podman_machine_dns()
        assert result == "192.168.127.1"

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_handles_multiple_nameservers(self, mock_is_macos, mock_run):
        """get_podman_machine_dns returns first nameserver when multiple exist."""
        mock_is_macos.return_value = True
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "machine", "ssh", "grep", "nameserver", "/etc/resolv.conf"],
            returncode=0,
            stdout="nameserver 192.168.127.1\nnameserver 8.8.8.8\n",
            stderr="",
        )
        result = get_podman_machine_dns()
        assert result == "192.168.127.1"

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_returns_none_on_empty_output(self, mock_is_macos, mock_run):
        """get_podman_machine_dns returns None when no nameserver found."""
        mock_is_macos.return_value = True
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "machine", "ssh", "grep", "nameserver", "/etc/resolv.conf"],
            returncode=1,
            stdout="",
            stderr="",
        )
        result = get_podman_machine_dns()
        assert result is None

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_warns_on_no_machine(self, mock_is_macos, mock_run, capsys):
        """get_podman_machine_dns warns when no Podman machine is found."""
        mock_is_macos.return_value = True
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "machine", "inspect"],
            returncode=1,
            stdout="",
            stderr="",
        )
        result = get_podman_machine_dns()
        assert result is None
        captured = capsys.readouterr()
        assert "No Podman machine found" in captured.err

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_warns_on_subprocess_error(self, mock_is_macos, mock_run, capsys):
        """get_podman_machine_dns warns when subprocess raises an error."""
        mock_is_macos.return_value = True
        mock_run.side_effect = subprocess.SubprocessError("command failed")
        result = get_podman_machine_dns()
        assert result is None
        captured = capsys.readouterr()
        assert "Failed to extract DNS from Podman VM" in captured.err

    @patch("paude.platform.subprocess.run")
    @patch("paude.platform.is_macos")
    def test_warns_on_empty_resolv_conf(self, mock_is_macos, mock_run, capsys):
        """get_podman_machine_dns warns when resolv.conf has no nameserver."""
        mock_is_macos.return_value = True
        # First call: podman machine inspect succeeds
        # Second call: podman machine ssh grep returns success but no nameserver
        mock_run.side_effect = [
            subprocess.CompletedProcess(
                args=["podman", "machine", "inspect"],
                returncode=0,
                stdout="{}",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[
                    "podman",
                    "machine",
                    "ssh",
                    "grep",
                    "nameserver",
                    "/etc/resolv.conf",
                ],
                returncode=0,
                stdout="search localdomain\n",
                stderr="",
            ),
        ]
        result = get_podman_machine_dns()
        assert result is None
        captured = capsys.readouterr()
        assert "No nameserver found in Podman VM resolv.conf" in captured.err

    @patch("paude.platform.is_macos")
    def test_no_warning_on_linux(self, mock_is_macos, capsys):
        """get_podman_machine_dns does not warn when not on macOS."""
        mock_is_macos.return_value = False
        result = get_podman_machine_dns()
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == ""
