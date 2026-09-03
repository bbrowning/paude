"""Tests for the allowed-domains subcommand (get/update domain operations)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError
from paude.backends.podman.proxy import PodmanProxyManager
from tests.fakes import make_backend

# ---------------------------------------------------------------------------
# ProxyManager: get_deployment_domains
# ---------------------------------------------------------------------------
# PodmanBackend: get_allowed_domains
# ---------------------------------------------------------------------------


class TestPodmanGetAllowedDomains:
    """Tests for PodmanBackend.get_allowed_domains method."""

    def test_returns_none_when_no_proxy(self) -> None:
        """get_allowed_domains returns None when no proxy exists (unrestricted)."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )

        backend = make_backend(mock_runner)

        result = backend.get_allowed_domains("my-session")
        assert result is None

    def test_returns_domain_list_when_proxy_exists(self) -> None:
        """get_allowed_domains returns domains from proxy container env."""
        mock_runner = MagicMock()
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_env.return_value = ".googleapis.com,.pypi.org"

        backend = make_backend(mock_runner)

        result = backend.get_allowed_domains("my-session")

        assert result == [".googleapis.com", ".pypi.org"]
        mock_runner.get_container_env.assert_called_once_with(
            "paude-proxy-my-session", "ALLOWED_DOMAINS"
        )

    def test_returns_empty_list_when_proxy_has_no_domains(self) -> None:
        """get_allowed_domains returns empty list when ALLOWED_DOMAINS is empty."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_env.return_value = ""

        backend = make_backend(mock_runner)

        result = backend.get_allowed_domains("my-session")
        assert result == []

    def test_raises_session_not_found(self) -> None:
        """get_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.get_allowed_domains("nonexistent")


# ---------------------------------------------------------------------------
# PodmanBackend: update_allowed_domains
# ---------------------------------------------------------------------------


class TestPodmanUpdateAllowedDomains:
    """Tests for PodmanBackend.update_allowed_domains method."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    def test_recreates_proxy_with_new_domains(self, mock_dns: MagicMock) -> None:
        """update_allowed_domains recreates proxy with new domain list."""
        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"

        def run(*args: str, **_kwargs: object) -> MagicMock:
            if args[:3] == ("inspect", "-f", "{{json .Config.CreateCommand}}"):
                return MagicMock(
                    returncode=0, stdout=json.dumps(["podman", "create"]), stderr=""
                )
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
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        # Disable CA verification polling (not the focus of this test)
        mock_runner.container_running.return_value = False
        mock_dns.return_value = None

        backend = make_backend(mock_runner)
        mock_network = MagicMock()
        mock_network.get_network_gateway.return_value = "10.89.0.1"
        backend._proxy = PodmanProxyManager(mock_runner, mock_network)
        backend._network_manager = mock_network

        backend.update_allowed_domains(
            "my-session", [".googleapis.com", ".example.com"]
        )

        # Verify proxy was recreated via engine.run (create + start)
        engine_calls = [str(c) for c in mock_runner.engine.run.call_args_list]
        assert any("create" in c for c in engine_calls)
        assert any("start" in c for c in engine_calls)

    def test_raises_session_not_found(self) -> None:
        """update_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False

        backend = make_backend(mock_runner)

        with pytest.raises(PodmanSessionNotFoundError):
            backend.update_allowed_domains("nonexistent", [".example.com"])

    def test_raises_value_error_when_no_proxy(self) -> None:
        """update_allowed_domains raises ValueError when session has no proxy."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = lambda name: (
            name == "paude-my-session"
        )

        backend = make_backend(mock_runner)

        with pytest.raises(ValueError, match="no proxy"):
            backend.update_allowed_domains("my-session", [".example.com"])

    @patch(
        "paude.backends.podman.helpers.get_session_credential_providers",
        return_value=["anthropic-oauth"],
    )
    @patch("paude.backends.podman.backend.get_session_composition")
    def test_default_update_never_gathers_ambient_credentials(
        self,
        mock_composition: MagicMock,
        mock_providers: MagicMock,
    ) -> None:
        runner = MagicMock()
        runner.container_exists.return_value = True
        backend = make_backend(runner)
        backend._proxy = MagicMock()
        backend._setup.gather_proxy_credentials = MagicMock()  # type: ignore[method-assign]
        composition = MagicMock()
        composition.agents = []
        mock_composition.return_value = composition

        backend.update_allowed_domains("my-session", [".example.com"])

        backend._setup.gather_proxy_credentials.assert_not_called()
        credentials = backend._proxy.update_domains.call_args.kwargs["credentials"]
        assert credentials.environment == {}

    @patch(
        "paude.backends.podman.helpers.get_session_credential_providers",
        return_value=["anthropic-oauth"],
    )
    @patch("paude.backends.podman.backend.get_session_composition")
    def test_explicit_refresh_gathers_a_host_overlay(
        self,
        mock_composition: MagicMock,
        mock_providers: MagicMock,
    ) -> None:
        from paude.backends.proxy_config import ProxyCredentials

        runner = MagicMock()
        runner.container_exists.return_value = True
        backend = make_backend(runner)
        backend._proxy = MagicMock()
        composition = MagicMock()
        composition.agents = []
        mock_composition.return_value = composition
        fresh = ProxyCredentials(environment={"CLAUDE_CODE_OAUTH_TOKEN": "fresh"})
        backend._setup.gather_proxy_credentials = MagicMock(  # type: ignore[method-assign]
            return_value=fresh
        )

        backend.update_allowed_domains(
            "my-session", [".example.com"], refresh_credentials=True
        )

        backend._setup.gather_proxy_credentials.assert_called_once_with(
            composition, ["anthropic-oauth"]
        )
        assert backend._proxy.update_domains.call_args.kwargs["credentials"] is fresh
