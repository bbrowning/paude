"""Tests for PodmanProxyManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.backends.podman.proxy import PodmanProxyManager, _get_host_dns


def _make_mock_runner(engine_binary: str = "podman") -> MagicMock:
    """Create a mock ContainerRunner with a proper engine."""
    mock_runner = MagicMock()
    mock_runner.engine.binary = engine_binary
    mock_runner.engine.is_remote = False
    mock_runner.engine.supports_multi_network_create = engine_binary != "docker"
    mock_runner.engine.default_bridge_network = (
        "podman" if engine_binary == "podman" else "bridge"
    )
    mock_runner.engine.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return mock_runner


class TestPodmanProxyManagerDnsLogging:
    """Tests for DNS logging in PodmanProxyManager (local Podman on macOS)."""

    @patch("paude.backends.podman.proxy.is_macos", return_value=True)
    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_logs_dns_when_available(
        self, mock_dns: MagicMock, mock_macos: MagicMock, capsys
    ) -> None:
        """create_proxy logs the DNS IP when extraction succeeds."""
        mock_dns.return_value = "192.168.127.1"
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        captured = capsys.readouterr()
        assert "Using Podman VM DNS: 192.168.127.1" in captured.err

    @patch("paude.backends.podman.proxy.is_macos", return_value=True)
    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_no_dns_log_when_none(
        self, mock_dns: MagicMock, mock_macos: MagicMock, capsys
    ) -> None:
        """create_proxy does not log DNS when extraction returns None."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        captured = capsys.readouterr()
        assert "Using Podman VM DNS" not in captured.err

    @patch("paude.backends.podman.proxy.is_macos", return_value=True)
    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_update_domains_logs_dns_when_available(
        self, mock_dns: MagicMock, mock_macos: MagicMock, capsys
    ) -> None:
        """update_domains logs the DNS IP when extraction succeeds."""
        mock_dns.return_value = "10.0.2.3"
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com", ".pypi.org"],
        )

        captured = capsys.readouterr()
        assert "Using Podman VM DNS: 10.0.2.3" in captured.err

    @patch("paude.backends.podman.proxy.is_macos", return_value=True)
    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_start_if_needed_logs_dns_on_recreate(
        self, mock_dns: MagicMock, mock_macos: MagicMock, capsys
    ) -> None:
        """start_if_needed logs DNS when recreating a missing proxy."""
        mock_dns.return_value = "192.168.127.1"
        mock_runner = _make_mock_runner()
        # Proxy container does not exist
        mock_runner.container_exists.return_value = False
        # But main container has proxy labels
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-test-session"],
                "Labels": {
                    "paude.io/session-name": "test-session",
                    "paude.io/allowed-domains": ".googleapis.com",
                    "paude.io/proxy-image": "proxy:latest",
                },
            }
        ]
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.start_if_needed("test-session")

        captured = capsys.readouterr()
        assert "Using Podman VM DNS: 192.168.127.1" in captured.err


class TestGetHostDns:
    """Tests for _get_host_dns across engine/platform combinations."""

    def test_docker_reads_resolv_conf_via_transport(self, capsys) -> None:
        """Docker engine reads DNS from host resolv.conf via transport."""
        engine = MagicMock()
        engine.binary = "docker"
        engine.transport.run.return_value = MagicMock(
            returncode=0, stdout="nameserver 10.0.0.1\nnameserver 8.8.8.8\n"
        )

        result = _get_host_dns(engine)

        assert result == "10.0.0.1"
        engine.transport.run.assert_called_once_with(
            ["grep", "nameserver", "/etc/resolv.conf"], check=False
        )
        captured = capsys.readouterr()
        assert "Using host DNS: 10.0.0.1" in captured.err

    def test_docker_skips_loopback_dns(self, capsys) -> None:
        """Docker engine skips 127.x.x.x nameservers (e.g. systemd-resolved)."""
        engine = MagicMock()
        engine.binary = "docker"
        engine.transport.run.return_value = MagicMock(
            returncode=0,
            stdout="nameserver 127.0.0.53\nnameserver 10.0.0.1\n",
        )

        result = _get_host_dns(engine)

        assert result == "10.0.0.1"

    def test_docker_returns_none_when_only_loopback(self) -> None:
        """Returns None when all nameservers are loopback."""
        engine = MagicMock()
        engine.binary = "docker"
        engine.transport.run.return_value = MagicMock(
            returncode=0, stdout="nameserver 127.0.0.53\n"
        )

        result = _get_host_dns(engine)

        assert result is None

    def test_docker_returns_none_on_failure(self) -> None:
        """Returns None when resolv.conf read fails."""
        engine = MagicMock()
        engine.binary = "docker"
        engine.transport.run.return_value = MagicMock(returncode=1, stdout="")

        result = _get_host_dns(engine)

        assert result is None

    def test_docker_returns_none_on_exception(self) -> None:
        """Returns None when transport raises an exception."""
        engine = MagicMock()
        engine.binary = "docker"
        engine.transport.run.side_effect = Exception("SSH connection failed")

        result = _get_host_dns(engine)

        assert result is None

    @patch("paude.backends.podman.proxy.is_macos", return_value=True)
    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_local_podman_macos_uses_vm_dns(
        self, mock_dns: MagicMock, mock_macos: MagicMock
    ) -> None:
        """Local Podman on macOS uses get_podman_machine_dns."""
        mock_dns.return_value = "192.168.127.1"
        engine = MagicMock()
        engine.binary = "podman"
        engine.is_remote = False

        result = _get_host_dns(engine)

        assert result == "192.168.127.1"
        engine.transport.run.assert_not_called()

    @patch("paude.backends.podman.proxy.is_macos", return_value=False)
    def test_local_podman_linux_reads_resolv_conf(
        self, mock_macos: MagicMock, capsys
    ) -> None:
        """Local Podman on Linux reads DNS from resolv.conf."""
        engine = MagicMock()
        engine.binary = "podman"
        engine.is_remote = False
        engine.transport.run.return_value = MagicMock(
            returncode=0, stdout="nameserver 10.0.0.1\n"
        )

        result = _get_host_dns(engine)

        assert result == "10.0.0.1"
        engine.transport.run.assert_called_once()
        captured = capsys.readouterr()
        assert "Using host DNS: 10.0.0.1" in captured.err

    def test_remote_podman_reads_resolv_conf(self, capsys) -> None:
        """Remote Podman reads DNS from remote host's resolv.conf."""
        engine = MagicMock()
        engine.binary = "podman"
        engine.is_remote = True
        engine.transport.run.return_value = MagicMock(
            returncode=0, stdout="nameserver 172.16.0.1\n"
        )

        result = _get_host_dns(engine)

        assert result == "172.16.0.1"
        engine.transport.run.assert_called_once()
