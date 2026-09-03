"""Tests for PodmanProxyManager."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.labels import (
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_ENDPOINTS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
)
from paude.backends.podman.proxy import (
    _PROXY_IP_POLL_ATTEMPTS,
    CA_CERT_CONTAINER_PATH,
    PodmanProxyManager,
    _get_host_dns,
    auth_volume_name,
    ca_volume_name,
)
from paude.backends.podman.proxy_credentials import PreparedProxyCredentials
from paude.backends.proxy_config import ProxyCredentials, derive_agent_ip
from paude.container.proxy_runner import ProxyStartError


def _make_mock_runner(engine_binary: str = "podman") -> MagicMock:
    """Create a mock ContainerRunner with a proper engine."""
    mock_runner = MagicMock()
    mock_runner.engine.binary = engine_binary
    mock_runner.engine.is_remote = False
    mock_runner.engine.is_podman = engine_binary != "docker"
    mock_runner.engine.supports_multi_network_create = engine_binary != "docker"
    mock_runner.engine.supports_secrets = engine_binary != "docker"
    mock_runner.engine.default_bridge_network = (
        "podman" if engine_binary == "podman" else "bridge"
    )

    def run(*args: str, **_kwargs: object) -> MagicMock:
        if args[:3] == ("inspect", "-f", "{{json .Config.CreateCommand}}"):
            return MagicMock(returncode=0, stdout=json.dumps([engine_binary, "create"]))
        if args[:3] == ("inspect", "-f", "{{.State.Running}}"):
            return MagicMock(returncode=0, stdout="true\n", stderr="")
        if args and args[0] == "run" and any("test -e" in arg for arg in args):
            if any("printf" in arg for arg in args):
                return MagicMock(
                    returncode=0,
                    stdout="\0".join(["0", "", "0", "", ""]),
                    stderr="",
                )
            return MagicMock(returncode=3, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_runner.engine.run.side_effect = run
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
        mock_network.get_network_gateway.return_value = "10.89.0.1"

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
        mock_network.get_network_gateway.return_value = "10.89.0.1"

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
        # Disable CA verification (not the focus of this test)
        mock_runner.container_running.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

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
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.start_if_needed("test-session")

        captured = capsys.readouterr()
        assert "Using Podman VM DNS: 192.168.127.1" in captured.err


class TestProxyManagerFixedIp:
    """Tests for PodmanProxyManager proxy IP derivation."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_returns_network_and_ip(self, mock_dns: MagicMock) -> None:
        """create_proxy returns (network_name, proxy_ip) tuple."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        nname, proxy_ip = manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        assert nname == "paude-net-test-session"
        assert proxy_ip == "10.89.0.2"

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_docker_returns_none_ip_when_no_gateway(
        self, mock_dns: MagicMock
    ) -> None:
        """On Docker, create_proxy returns None IP (hostname fallback is ok)."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("docker")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        nname, proxy_ip = manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        assert nname == "paude-net-test-session"
        assert proxy_ip is None
        mock_network.remove_network.assert_not_called()

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_raises_on_podman_when_ip_unavailable(
        self, mock_dns: MagicMock
    ) -> None:
        """On Podman, create_proxy aborts and removes the network if no IP."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with pytest.raises(ProxyStartError):
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )

        mock_network.remove_network.assert_called_once_with("paude-net-test-session")

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_passes_ip_to_proxy_runner(self, mock_dns: MagicMock) -> None:
        """create_proxy passes the fixed IP to create_session_proxy."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "172.28.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        # Check IP was embedded in --network spec (Podman multi-network)
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [
            c for c in engine_calls if c[0] and len(c[0]) > 0 and c[0][0] == "create"
        ]
        assert create_call, "Expected a create call"
        call_args = create_call[0][0]
        assert "--ip" not in call_args
        # IP embedded in first --network, bridge as separate --network
        net_indices = [i for i, a in enumerate(call_args) if a == "--network"]
        assert len(net_indices) == 2
        assert "ip=172.28.0.2" in call_args[net_indices[0] + 1]


class TestResolveProxyIpGuard:
    """Direct tests for the shared _resolve_proxy_ip reachability guard."""

    def test_raises_and_removes_network_when_owned(self) -> None:
        """On disable-dns with no IP, raise and remove a just-created network."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with pytest.raises(ProxyStartError):
            manager._resolve_proxy_ip(
                "paude-net-test", disable_dns=True, remove_network_on_failure=True
            )

        mock_network.remove_network.assert_called_once_with("paude-net-test")

    def test_raises_without_removing_live_network(self) -> None:
        """A live network is not torn down on a transient inspect failure."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with pytest.raises(ProxyStartError):
            manager._resolve_proxy_ip(
                "paude-net-test", disable_dns=True, remove_network_on_failure=False
            )

        mock_network.remove_network.assert_not_called()

    def test_returns_none_without_raising_when_dns_enabled(self) -> None:
        """With DNS the hostname fallback is legitimate, so None passes through."""
        mock_runner = _make_mock_runner("docker")
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        result = manager._resolve_proxy_ip(
            "paude-net-test", disable_dns=False, remove_network_on_failure=True
        )

        assert result is None
        mock_network.remove_network.assert_not_called()

    def test_returns_derived_ip_on_success(self) -> None:
        """The happy path returns the derived proxy IP."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        result = manager._resolve_proxy_ip(
            "paude-net-test", disable_dns=True, remove_network_on_failure=True
        )

        assert result == "10.89.0.2"
        mock_network.remove_network.assert_not_called()

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_start_if_needed_raises_on_podman_when_ip_unavailable(
        self, mock_dns: MagicMock
    ) -> None:
        """Recreating a missing proxy aborts without IP, but leaves the
        network alone since the session's agent container may still be
        attached to it."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        # Proxy container is gone -> recreate path
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch.object(
            manager,
            "get_config_from_labels",
            return_value=("proxy:latest", [".googleapis.com"], [], []),
        ):
            with pytest.raises(ProxyStartError):
                manager.start_if_needed(session_name="test-session")

        mock_network.remove_network.assert_not_called()
        # A silently-broken proxy must never be created
        create_calls = [
            c
            for c in mock_runner.engine.run.call_args_list
            if c[0] and c[0][0] == "create"
        ]
        assert not create_calls

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_raises_on_podman_when_ip_unavailable(
        self, mock_dns: MagicMock
    ) -> None:
        """update_domains aborts without tearing down the live network."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with pytest.raises(ProxyStartError):
            manager.update_domains(
                session_name="test-session",
                domains=[".googleapis.com"],
            )

        # The live network must survive a transient inspect failure
        mock_network.remove_network.assert_not_called()


class TestProxyIpRetry:
    """Tests for the bounded retry in PodmanProxyManager._get_proxy_ip."""

    @patch("paude.backends.podman.proxy.time.sleep")
    def test_get_proxy_ip_retries_until_gateway_available(
        self, mock_sleep: MagicMock
    ) -> None:
        """A transient empty inspect is retried until the gateway appears."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.side_effect = [None, None, "10.89.0.1"]

        manager = PodmanProxyManager(mock_runner, mock_network)
        result = manager._get_proxy_ip("paude-net-test")

        assert result == "10.89.0.2"
        assert mock_network.get_network_gateway.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.25)

    @patch("paude.backends.podman.proxy.time.sleep")
    def test_get_proxy_ip_returns_none_after_exhausting_attempts(
        self, mock_sleep: MagicMock
    ) -> None:
        """After all attempts fail, _get_proxy_ip returns None."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        result = manager._get_proxy_ip("paude-net-test")

        assert result is None
        assert mock_network.get_network_gateway.call_count == _PROXY_IP_POLL_ATTEMPTS
        assert mock_sleep.call_count == _PROXY_IP_POLL_ATTEMPTS - 1

    @patch("paude.backends.podman.proxy.time.sleep")
    def test_get_proxy_ip_no_sleep_on_first_success(
        self, mock_sleep: MagicMock
    ) -> None:
        """The happy path returns on the first attempt without sleeping."""
        mock_runner = _make_mock_runner()
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        result = manager._get_proxy_ip("paude-net-test")

        assert result == "10.89.0.2"
        assert mock_network.get_network_gateway.call_count == 1
        mock_sleep.assert_not_called()


class TestProxyManagerDisableDns:
    """Tests for disable_dns on network creation in PodmanProxyManager."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_disables_dns_for_podman(self, mock_dns: MagicMock) -> None:
        """create_proxy passes disable_dns=True for Podman engine."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("podman")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        mock_network.create_internal_network.assert_called_once_with(
            "paude-net-test-session", disable_dns=True
        )

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_keeps_dns_for_docker(self, mock_dns: MagicMock) -> None:
        """create_proxy passes disable_dns=False for Docker engine."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("docker")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "172.17.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.create_proxy(
            session_name="test-session",
            proxy_image="proxy:latest",
            allowed_domains=[".googleapis.com"],
        )

        mock_network.create_internal_network.assert_called_once_with(
            "paude-net-test-session", disable_dns=False
        )


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


class TestCaVolumeName:
    """Tests for ca_volume_name helper."""

    def test_ca_volume_name_format(self) -> None:
        assert ca_volume_name("my-session") == "paude-ca-my-session"


class TestDistributeCaCert:
    """Tests for PodmanProxyManager.distribute_ca_cert."""

    def test_distribute_ca_cert_copies_cert(self) -> None:
        """distribute_ca_cert reads cert from proxy and injects into agent."""
        mock_runner = _make_mock_runner()
        mock_runner.container_running.return_value = True
        # First exec: test -f succeeds (cert exists)
        # Second exec: cat returns cert content
        # Third exec: build custom CA bundle succeeds
        mock_runner.exec_in_container.side_effect = [
            MagicMock(returncode=0),  # test -f
            MagicMock(
                returncode=0,
                stdout="-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n",
            ),  # cat
            MagicMock(returncode=0),  # build CA bundle
        ]
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.distribute_ca_cert("test-session")

        # Verify inject_file was called with cert content
        mock_runner.inject_file.assert_called_once()
        call_args = mock_runner.inject_file.call_args
        assert call_args[0][0] == "paude-test-session"  # agent container
        assert "BEGIN CERTIFICATE" in call_args[0][1]
        assert call_args[0][2] == CA_CERT_CONTAINER_PATH

    def test_distribute_ca_cert_skips_when_proxy_not_running(self) -> None:
        """distribute_ca_cert is a no-op if proxy is not running."""
        mock_runner = _make_mock_runner()
        mock_runner.container_running.side_effect = lambda name: (
            name != "paude-proxy-test-session"
        )
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.distribute_ca_cert("test-session")

        mock_runner.inject_file.assert_not_called()

    def test_distribute_ca_cert_skips_when_agent_not_running(self) -> None:
        """distribute_ca_cert is a no-op if agent container is not running."""
        mock_runner = _make_mock_runner()
        mock_runner.container_running.side_effect = lambda name: (
            name != "paude-test-session"
        )
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.distribute_ca_cert("test-session")

        mock_runner.inject_file.assert_not_called()

    def test_distribute_ca_cert_warns_on_timeout(self, capsys) -> None:
        """distribute_ca_cert warns if CA cert not generated in time."""
        mock_runner = _make_mock_runner()
        mock_runner.container_running.return_value = True
        # test -f always fails (cert never appears)
        mock_runner.exec_in_container.return_value = MagicMock(returncode=1)
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.backends.podman.ca_cert.CA_CERT_POLL_TIMEOUT", 0):
            manager.distribute_ca_cert("test-session")

        captured = capsys.readouterr()
        assert "Timed out waiting for proxy CA certificate" in captured.err
        mock_runner.inject_file.assert_not_called()


class TestBuildCaBundleCmd:
    """Tests for _BUILD_CA_BUNDLE_CMD multi-distro support."""

    def test_cmd_supports_debian_ca_path(self) -> None:
        """_BUILD_CA_BUNDLE_CMD must probe Debian CA bundle path."""
        from paude.backends.podman.proxy import _BUILD_CA_BUNDLE_CMD

        assert "/etc/ssl/certs/ca-certificates.crt" in _BUILD_CA_BUNDLE_CMD

    def test_cmd_supports_centos_ca_path(self) -> None:
        """_BUILD_CA_BUNDLE_CMD must probe RHEL/CentOS CA bundle path."""
        from paude.backends.podman.proxy import _BUILD_CA_BUNDLE_CMD

        assert (
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem" in _BUILD_CA_BUNDLE_CMD
        )

    def test_cmd_supports_alpine_ca_path(self) -> None:
        """_BUILD_CA_BUNDLE_CMD must probe Alpine CA bundle path."""
        from paude.backends.podman.proxy import _BUILD_CA_BUNDLE_CMD

        assert "/etc/ssl/cert.pem" in _BUILD_CA_BUNDLE_CMD

    def test_cmd_writes_to_ca_bundle_path(self) -> None:
        """_BUILD_CA_BUNDLE_CMD must write to CA_BUNDLE_PATH."""
        from paude.backends.podman.proxy import _BUILD_CA_BUNDLE_CMD
        from paude.backends.proxy_config import CA_BUNDLE_PATH

        assert CA_BUNDLE_PATH in _BUILD_CA_BUNDLE_CMD


class TestCreateProxyCaVolume:
    """Tests for CA volume creation in PodmanProxyManager.create_proxy."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_creates_ca_volume(self, mock_dns: MagicMock) -> None:
        """create_proxy creates a named CA volume and passes it to the proxy."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager") as mock_vm_cls:
            mock_vm = mock_vm_cls.return_value
            mock_vm.volume_exists.return_value = False
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )
            assert [call.args for call in mock_vm.create_volume.call_args_list] == [
                ("paude-ca-test-session",),
                (auth_volume_name("test-session"),),
            ]

        # Check that -v ca_volume:/data/ca is in the create call
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if "create" in str(c)]
        assert create_call
        call_args = create_call[0][0]
        vol_indices = [i for i, a in enumerate(call_args) if a == "-v"]
        vol_args = [call_args[i + 1] for i in vol_indices]
        assert "paude-ca-test-session:/data/ca" in vol_args
        assert "paude-auth-test-session:/data/auth" in vol_args

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_reuses_existing_auth_volume(
        self, mock_dns: MagicMock
    ) -> None:
        """create_proxy preserves an existing OAuth state volume."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager") as mock_vm_cls:
            mock_vm = mock_vm_cls.return_value
            mock_vm.volume_exists.return_value = True
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )

            mock_vm.create_volume.assert_called_once_with("paude-ca-test-session")

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_keeps_existing_auth_volume_on_failure(
        self, mock_dns: MagicMock
    ) -> None:
        """Failed proxy creation does not delete existing OAuth state."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager") as mock_vm_cls:
            mock_vm = mock_vm_cls.return_value
            mock_vm.volume_exists.return_value = True
            with (
                patch.object(
                    manager._proxy_runner,
                    "create_session_proxy",
                    side_effect=RuntimeError("proxy failed"),
                ),
                pytest.raises(RuntimeError, match="^proxy failed$"),
            ):
                manager.create_proxy(
                    session_name="test-session",
                    proxy_image="proxy:latest",
                    allowed_domains=[".googleapis.com"],
                )

            mock_vm.remove_volume.assert_called_once_with(
                "paude-ca-test-session", force=True
            )


class TestProxyCredentials:
    """Tests for credential passing to the proxy container."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_passes_credentials_as_secrets_for_podman(
        self, mock_dns: MagicMock
    ) -> None:
        """create_proxy uses --secret flags for podman (not -e)."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("podman")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager"):
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
                credentials={
                    "ANTHROPIC_API_KEY": "sk-real-key",
                    "GH_TOKEN": "ghp_real",
                },
            )

        # Secrets should have been created
        assert mock_runner.create_secret_from_value.call_count == 2
        mock_runner.create_secret_from_value.assert_any_call(
            "paude-proxy-cred-test-session-anthropic-api-key", "sk-real-key"
        )
        mock_runner.create_secret_from_value.assert_any_call(
            "paude-proxy-cred-test-session-gh-token", "ghp_real"
        )

        # Check that --secret flags appear in the create call (not -e for creds)
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]

        secret_indices = [i for i, a in enumerate(call_args) if a == "--secret"]
        secret_vals = [call_args[i + 1] for i in secret_indices]
        assert (
            "paude-proxy-cred-test-session-anthropic-api-key,"
            "type=env,target=ANTHROPIC_API_KEY" in secret_vals
        )
        assert (
            "paude-proxy-cred-test-session-gh-token,"
            "type=env,target=GH_TOKEN" in secret_vals
        )

        # Credentials should NOT appear as -e flags
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert not any("ANTHROPIC_API_KEY" in v for v in env_vals)
        assert not any("GH_TOKEN" in v for v in env_vals)

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_passes_credentials_as_env_vars_for_docker(
        self, mock_dns: MagicMock
    ) -> None:
        """create_proxy falls back to -e flags for Docker (no secret support)."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("docker")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "172.17.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager"):
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
                credentials={
                    "ANTHROPIC_API_KEY": "sk-real-key",
                    "GH_TOKEN": "ghp_real",
                },
            )

        # No secrets created for Docker
        mock_runner.create_secret_from_value.assert_not_called()

        # Credentials should appear as -e flags
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert "ANTHROPIC_API_KEY=sk-real-key" in env_vals
        assert "GH_TOKEN=ghp_real" in env_vals

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_without_credentials(self, mock_dns: MagicMock) -> None:
        """create_proxy works without credentials (backward compat)."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager"):
            nname, proxy_ip = manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )

        assert nname == "paude-net-test-session"
        assert proxy_ip == "10.89.0.2"

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_passes_credentials_as_secrets(
        self, mock_dns: MagicMock
    ) -> None:
        """update_domains passes credentials via --secret for podman."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("podman")
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        # Disable CA verification (not the focus of this test)
        mock_runner.container_running.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com"],
            credentials={"ANTHROPIC_API_KEY": "sk-real-key"},
        )

        # Refresh uses a generation-specific secret, leaving the old binding
        # available until the proxy transaction commits.
        mock_runner.create_secret_from_value.assert_called_once()
        staged_name, staged_value = mock_runner.create_secret_from_value.call_args.args
        assert staged_name.startswith(
            "paude-proxy-cred-test-session-anthropic-api-key-update-"
        )
        assert staged_value == "sk-real-key"

        # Check --secret in the recreate/create call
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        secret_indices = [i for i, a in enumerate(call_args) if a == "--secret"]
        secret_vals = [call_args[i + 1] for i in secret_indices]
        assert f"{staged_name},type=env,target=ANTHROPIC_API_KEY" in secret_vals


class TestUpdateDomainsCaResilience:
    """Tests for CA cert resilience across proxy recreates in update_domains."""

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_passes_ca_volume_to_recreate(
        self, mock_dns: MagicMock
    ) -> None:
        """update_domains passes the CA volume name to recreate_session_proxy."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        cert = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
        mock_runner.exec_in_container.side_effect = [
            MagicMock(returncode=0),  # test -f in proxy
            MagicMock(returncode=0, stdout=cert),  # cat in proxy
            MagicMock(returncode=0, stdout=cert),  # cat in agent
        ]
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com", ".pypi.org"],
        )

        # Verify CA volume is in the create call
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        vol_indices = [i for i, a in enumerate(call_args) if a == "-v"]
        vol_args = [call_args[i + 1] for i in vol_indices]
        assert "paude-ca-test-session:/data/ca" in vol_args

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_skips_redistribution_when_cert_matches(
        self, mock_dns: MagicMock, capsys
    ) -> None:
        """update_domains does not redistribute CA cert when it matches."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        cert = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
        mock_runner.exec_in_container.side_effect = [
            MagicMock(returncode=0),  # test -f in proxy
            MagicMock(returncode=0, stdout=cert),  # cat proxy cert
            MagicMock(returncode=0, stdout=cert),  # cat agent cert (matches)
        ]
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com"],
        )

        captured = capsys.readouterr()
        assert "Redistributing CA certificate" not in captured.err
        mock_runner.inject_file.assert_not_called()

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_redistributes_when_cert_differs(
        self, mock_dns: MagicMock, capsys
    ) -> None:
        """update_domains redistributes CA cert when agent has stale cert."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        new_cert = "-----BEGIN CERTIFICATE-----\nnew\n-----END CERTIFICATE-----\n"
        old_cert = "-----BEGIN CERTIFICATE-----\nold\n-----END CERTIFICATE-----\n"
        mock_runner.exec_in_container.side_effect = [
            # _redistribute_ca_if_needed checks
            MagicMock(returncode=0),  # test -f in proxy
            MagicMock(returncode=0, stdout=new_cert),  # cat proxy cert
            MagicMock(returncode=0, stdout=old_cert),  # cat agent cert (differs)
            # Direct inject (no re-poll) — just build CA bundle
            MagicMock(returncode=0),  # build CA bundle
        ]
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com"],
        )

        captured = capsys.readouterr()
        assert "Redistributing CA certificate" in captured.err
        mock_runner.inject_file.assert_called_once()

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_redistributes_when_agent_cert_missing(
        self, mock_dns: MagicMock, capsys
    ) -> None:
        """update_domains redistributes when agent has no CA cert."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        cert = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
        mock_runner.exec_in_container.side_effect = [
            # _redistribute_ca_if_needed checks
            MagicMock(returncode=0),  # test -f in proxy
            MagicMock(returncode=0, stdout=cert),  # cat proxy cert
            MagicMock(returncode=1, stdout=""),  # cat agent cert (missing)
            # Direct inject (no re-poll) — just build CA bundle
            MagicMock(returncode=0),  # build CA bundle
        ]
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com"],
        )

        captured = capsys.readouterr()
        assert "Redistributing CA certificate" in captured.err
        mock_runner.inject_file.assert_called_once()

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_warns_when_ca_cert_missing_after_recreate(
        self, mock_dns: MagicMock, capsys
    ) -> None:
        """update_domains warns if CA cert is missing after proxy recreate."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        # test -f always fails (cert never appears)
        mock_runner.exec_in_container.return_value = MagicMock(returncode=1)
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.backends.podman.ca_cert.CA_CERT_POLL_TIMEOUT", 0):
            manager.update_domains(
                session_name="test-session",
                domains=[".googleapis.com"],
            )

        captured = capsys.readouterr()
        assert "CA certificate missing after proxy recreate" in captured.err
        mock_runner.inject_file.assert_not_called()


class TestUpdateDomainTransaction:
    """Durable state and retained-proxy swap commit or roll back together."""

    @patch("paude.backends.podman.proxy._get_host_dns", return_value=None)
    def test_state_write_failure_restores_proxy_state_and_staged_secrets(
        self, mock_dns: MagicMock
    ) -> None:
        runner = _make_mock_runner()
        runner.container_exists.return_value = True
        runner.get_container_image.return_value = "proxy:latest"
        network = MagicMock()
        network.get_network_gateway.return_value = "10.89.0.1"
        manager = PodmanProxyManager(runner, network)
        manager.get_config_from_labels = MagicMock(  # type: ignore[method-assign]
            return_value=("proxy:latest", [".old.example"], [], [])
        )
        prepared = PreparedProxyCredentials(credentials=ProxyCredentials())
        manager._credentials = MagicMock()
        manager._credentials.prepare_update.return_value = prepared
        manager._credentials.credential_env.return_value = {}
        manager._state = MagicMock()
        manager._state.read_pair.return_value = ([".old.example"], None)
        manager._state.write.side_effect = RuntimeError("write failed")
        swap = MagicMock()
        manager._proxy_runner = MagicMock()
        manager._proxy_runner.swap_session_proxy.return_value = swap

        with pytest.raises(RuntimeError, match="write failed"):
            manager.update_domains("test-session", [".new.example"])

        manager._state.restore.assert_called_once_with(
            "paude-auth-test-session", "proxy:latest", [".old.example"]
        )
        swap.rollback.assert_called_once_with()
        manager._credentials.rollback_update.assert_called_once_with(prepared)
        swap.commit.assert_not_called()

    @patch("paude.backends.podman.proxy._get_host_dns", return_value=None)
    def test_success_commits_state_before_removing_retained_proxy(
        self, mock_dns: MagicMock
    ) -> None:
        runner = _make_mock_runner()
        runner.container_exists.return_value = True
        runner.get_container_image.return_value = "proxy:latest"
        network = MagicMock()
        network.get_network_gateway.return_value = "10.89.0.1"
        manager = PodmanProxyManager(runner, network)
        manager.get_config_from_labels = MagicMock(  # type: ignore[method-assign]
            return_value=("proxy:latest", [".old.example"], [], [])
        )
        prepared = PreparedProxyCredentials(credentials=ProxyCredentials())
        manager._credentials = MagicMock()
        manager._credentials.prepare_update.return_value = prepared
        manager._credentials.credential_env.return_value = {}
        manager._state = MagicMock()
        manager._state.read_pair.return_value = ([".old.example"], None)
        swap = MagicMock()
        manager._proxy_runner = MagicMock()
        manager._proxy_runner.swap_session_proxy.return_value = swap
        events = MagicMock()
        events.attach_mock(manager._state.write, "write")
        events.attach_mock(swap.commit, "commit")

        manager.update_domains("test-session", [".new.example"])

        assert [call[0] for call in events.mock_calls] == ["write", "commit"]
        manager._credentials.commit_update.assert_called_once_with(prepared)
        swap.rollback.assert_not_called()

    @patch("paude.backends.podman.proxy._get_host_dns", return_value=None)
    def test_domain_update_preserves_and_commits_endpoint_policy(
        self, mock_dns: MagicMock
    ) -> None:
        runner = _make_mock_runner()
        runner.container_exists.return_value = True
        runner.get_container_image.return_value = "proxy:latest"
        network = MagicMock()
        network.get_network_gateway.return_value = "10.89.0.1"
        manager = PodmanProxyManager(runner, network)
        manager.get_config_from_labels = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "proxy:latest",
                ["api.example.com"],
                ["api.example.com:8443"],
                [],
            )
        )
        manager._credentials = MagicMock()
        manager._credentials.prepare_update.return_value = PreparedProxyCredentials(
            credentials=ProxyCredentials()
        )
        manager._credentials.credential_env.return_value = {}
        manager._state = MagicMock()
        manager._endpoint_state = MagicMock()
        manager._state.read_pair.return_value = (["api.example.com"], None)
        manager._proxy_runner = MagicMock()
        manager._proxy_runner.swap_session_proxy.return_value = MagicMock()
        manager._ca_cert = MagicMock()

        manager.update_domains("test-session", ["new.example.com"])

        swap_args = manager._proxy_runner.swap_session_proxy.call_args.kwargs
        assert swap_args["allowed_endpoints"] == ["api.example.com:8443"]
        manager._endpoint_state.write.assert_called_once_with(
            "paude-auth-test-session",
            "proxy:latest",
            ["api.example.com:8443"],
        )


class TestUpdateEndpointsCapability:
    """Endpoint updates never silently reuse a pre-feature proxy image."""

    def test_legacy_session_requires_upgrade(self) -> None:
        runner = _make_mock_runner()
        runner.list_containers.return_value = [
            {
                "Labels": {
                    PAUDE_LABEL_SESSION: "test-session",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                    PAUDE_LABEL_PROXY_IMAGE: "proxy:legacy",
                }
            }
        ]
        manager = PodmanProxyManager(runner, MagicMock())
        manager._proxy_runner = MagicMock()

        with pytest.raises(ValueError, match="paude upgrade test-session"):
            manager.update_endpoints("test-session", ["api.example.com:8443"])

        manager._proxy_runner.swap_session_proxy.assert_not_called()

    @patch("paude.backends.podman.proxy._get_host_dns", return_value=None)
    def test_uses_configured_endpoint_capable_image(
        self, mock_dns: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runner = _make_mock_runner()
        runner.container_exists.return_value = True
        runner.get_container_image.return_value = "proxy:legacy"
        runner.get_container_env.return_value = "api.example.com"
        runner.list_containers.return_value = [
            {
                "Labels": {
                    PAUDE_LABEL_SESSION: "test-session",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                    PAUDE_LABEL_ENDPOINTS: "",
                    PAUDE_LABEL_PROXY_IMAGE: "proxy:endpoint-capable",
                }
            }
        ]
        network = MagicMock()
        network.get_network_gateway.return_value = "10.89.0.1"
        manager = PodmanProxyManager(runner, network)
        manager._credentials = MagicMock()
        manager._credentials.prepare_update.return_value = PreparedProxyCredentials(
            credentials=ProxyCredentials()
        )
        manager._credentials.credential_env.return_value = {}
        manager._state = MagicMock()
        manager._endpoint_state = MagicMock()
        manager._state.read_pair.return_value = (["api.example.com"], None)
        manager._proxy_runner = MagicMock()
        manager._proxy_runner.swap_session_proxy.return_value = MagicMock()
        manager._ca_cert = MagicMock()

        manager.update_endpoints("test-session", ["api.example.com:8443"])

        swap_args = manager._proxy_runner.swap_session_proxy.call_args.kwargs
        assert swap_args["image"] == "proxy:endpoint-capable"
        assert (
            "Updating proxy endpoints for session 'test-session'"
            in capsys.readouterr().err
        )
        manager._endpoint_state.write.assert_called_once_with(
            "paude-auth-test-session",
            "proxy:endpoint-capable",
            ["api.example.com:8443"],
        )


class TestSourceIpFiltering:
    """Tests for source IP filtering (allowed_clients) in proxy creation."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_passes_allowed_clients(self, mock_dns: MagicMock) -> None:
        """create_proxy derives agent IP and passes it as allowed_clients."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager"):
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )

        # proxy_ip = gateway + 1 = 10.89.0.2, agent_ip = proxy_ip + 1 = 10.89.0.3
        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert "PAUDE_PROXY_ALLOWED_CLIENTS=10.89.0.3" in env_vals

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_create_proxy_no_allowed_clients_when_no_gateway(
        self, mock_dns: MagicMock
    ) -> None:
        """create_proxy omits allowed_clients when gateway is unavailable.

        Uses Docker, where a missing IP is a legitimate hostname fallback;
        on Podman create_proxy raises instead (see TestProxyManagerFixedIp).
        """
        mock_dns.return_value = None
        mock_runner = _make_mock_runner("docker")
        mock_runner.container_exists.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = None

        manager = PodmanProxyManager(mock_runner, mock_network)
        with patch("paude.container.volume.VolumeManager"):
            manager.create_proxy(
                session_name="test-session",
                proxy_image="proxy:latest",
                allowed_domains=[".googleapis.com"],
            )

        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert not any("PAUDE_PROXY_ALLOWED_CLIENTS" in v for v in env_vals)

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_update_domains_passes_allowed_clients(self, mock_dns: MagicMock) -> None:
        """update_domains passes allowed_clients to the recreated proxy."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        mock_runner.container_running.return_value = False
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.update_domains(
            session_name="test-session",
            domains=[".googleapis.com"],
        )

        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert "PAUDE_PROXY_ALLOWED_CLIENTS=10.89.0.3" in env_vals

    @patch("paude.backends.podman.proxy._get_host_dns")
    def test_start_if_needed_recreate_passes_allowed_clients(
        self, mock_dns: MagicMock
    ) -> None:
        """start_if_needed passes allowed_clients when recreating missing proxy."""
        mock_dns.return_value = None
        mock_runner = _make_mock_runner()
        # Proxy doesn't exist, so it should be recreated from labels
        mock_runner.container_exists.return_value = False
        mock_runner.engine.run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"

        manager = PodmanProxyManager(mock_runner, mock_network)
        # Patch get_config_from_labels to return proxy config
        with patch.object(
            manager,
            "get_config_from_labels",
            return_value=("proxy:latest", [".googleapis.com"], [], []),
        ):
            manager.start_if_needed(
                session_name="test-session",
                credentials={"ANTHROPIC_API_KEY": "sk-real"},
            )

        engine_calls = mock_runner.engine.run.call_args_list
        create_call = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert create_call
        call_args = create_call[0][0]
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert "PAUDE_PROXY_ALLOWED_CLIENTS=10.89.0.3" in env_vals

    def test_derive_agent_ip(self) -> None:
        """derive_agent_ip returns proxy IP + 1."""
        assert derive_agent_ip("10.89.0.2") == "10.89.0.3"
        assert derive_agent_ip("172.16.0.10") == "172.16.0.11"


class TestStartIfNeededStoppedProxy:
    """Tests for start_if_needed with stopped proxies."""

    def test_stopped_proxy_is_started_not_recreated(self) -> None:
        """start_if_needed restarts a stopped proxy without recreating it."""
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.start_if_needed(session_name="test-session")

        engine_calls = mock_runner.engine.run.call_args_list
        start_calls = [c for c in engine_calls if c[0] and c[0][0] == "start"]
        assert start_calls, "Expected start call for stopped proxy"

        mock_runner.stop_container.assert_not_called()
        mock_runner.remove_container.assert_not_called()
        create_calls = [c for c in engine_calls if c[0] and c[0][0] == "create"]
        assert not create_calls, "Should not recreate a stopped proxy"


class TestStartIfNeededRunningProxy:
    """Tests for start_if_needed with running proxies."""

    def test_running_proxy_is_left_alone(self) -> None:
        """start_if_needed is a no-op when the proxy is already running."""
        mock_runner = _make_mock_runner()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_network = MagicMock()

        manager = PodmanProxyManager(mock_runner, mock_network)
        manager.start_if_needed(session_name="test-session")

        mock_runner.engine.run.assert_not_called()
        mock_runner.stop_container.assert_not_called()
        mock_runner.remove_container.assert_not_called()
