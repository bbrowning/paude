"""Tests for PodmanProxyManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.backends.podman.proxy import PodmanProxyManager


def _make_mock_runner() -> MagicMock:
    """Create a mock ContainerRunner with a proper engine."""
    mock_runner = MagicMock()
    mock_runner.engine.binary = "podman"
    mock_runner.engine.supports_multi_network_create = True
    mock_runner.engine.default_bridge_network = "podman"
    mock_runner.engine.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    return mock_runner


class TestPodmanProxyManagerDnsLogging:
    """Tests for DNS logging in PodmanProxyManager."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_logs_dns_when_available(
        self, mock_dns: MagicMock, capsys
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

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_no_dns_log_when_none(
        self, mock_dns: MagicMock, capsys
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

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_update_domains_logs_dns_when_available(
        self, mock_dns: MagicMock, capsys
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

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_start_if_needed_logs_dns_on_recreate(
        self, mock_dns: MagicMock, capsys
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
