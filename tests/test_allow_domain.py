"""Tests for the allowed-domains subcommand (get/update domain operations)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.openshift.backend import OpenShiftBackend
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import SessionNotFoundError
from paude.backends.openshift.oc import OcClient
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.podman import PodmanBackend
from paude.backends.podman import SessionNotFoundError as PodmanSessionNotFoundError

# ---------------------------------------------------------------------------
# ProxyManager: get_deployment_domains
# ---------------------------------------------------------------------------


class TestProxyManagerGetDomains:
    """Tests for ProxyManager.get_deployment_domains method."""

    def test_calls_oc_get_with_correct_jsonpath(self) -> None:
        """get_deployment_domains calls oc get with the ALLOWED_DOMAINS jsonpath."""
        oc = MagicMock(spec=OcClient)
        result = MagicMock(returncode=0, stdout=".googleapis.com,.pypi.org")
        oc.run.return_value = result

        pm = ProxyManager(oc, "test-ns")
        pm.get_deployment_domains("my-session")

        oc.run.assert_called_once()
        args = oc.run.call_args
        positional = args[0]

        # Should use "get" on the deployment
        assert "get" in positional
        assert "deployment/paude-proxy-my-session" in positional or (
            "deployment" in positional and "paude-proxy-my-session" in positional
        )

        # Should use jsonpath to extract ALLOWED_DOMAINS env value
        jsonpath_args = [a for a in positional if "jsonpath" in str(a).lower()]
        assert len(jsonpath_args) >= 1
        jsonpath_str = str(jsonpath_args[0])
        assert "ALLOWED_DOMAINS" in jsonpath_str or "env" in jsonpath_str

    def test_parses_comma_separated_domains(self) -> None:
        """get_deployment_domains parses comma-separated response into list."""
        oc = MagicMock(spec=OcClient)
        result = MagicMock(
            returncode=0,
            stdout=".googleapis.com,.pypi.org,.github.com",
        )
        oc.run.return_value = result

        pm = ProxyManager(oc, "test-ns")
        domains = pm.get_deployment_domains("my-session")

        assert domains == [".googleapis.com", ".pypi.org", ".github.com"]

    def test_single_domain(self) -> None:
        """get_deployment_domains handles a single domain correctly."""
        oc = MagicMock(spec=OcClient)
        result = MagicMock(returncode=0, stdout=".example.com")
        oc.run.return_value = result

        pm = ProxyManager(oc, "test-ns")
        domains = pm.get_deployment_domains("my-session")

        assert domains == [".example.com"]

    def test_empty_response_returns_empty_list(self) -> None:
        """get_deployment_domains returns empty list for empty response."""
        oc = MagicMock(spec=OcClient)
        result = MagicMock(returncode=0, stdout="")
        oc.run.return_value = result

        pm = ProxyManager(oc, "test-ns")
        domains = pm.get_deployment_domains("my-session")

        assert domains == []


# ---------------------------------------------------------------------------
# ProxyManager: update_deployment_domains
# ---------------------------------------------------------------------------


class TestProxyManagerUpdateDomains:
    """Tests for ProxyManager domain update operations via oc patch."""

    def test_update_domains_calls_oc_patch(self) -> None:
        """Updating domains calls oc patch on the proxy deployment."""
        oc = MagicMock(spec=OcClient)
        oc.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        pm = ProxyManager(oc, "test-ns")
        pm.update_deployment_domains("my-session", [".googleapis.com", ".pypi.org"])

        oc.run.assert_called_once()
        args = oc.run.call_args[0]

        assert "patch" in args
        assert "deployment/paude-proxy-my-session" in args
        assert "--type=strategic" in args

        # The patch should contain the ALLOWED_DOMAINS value
        patch_arg = [a for a in args if a.startswith("-p=")]
        assert len(patch_arg) == 1
        patch_data = json.loads(patch_arg[0][3:])
        env = patch_data["spec"]["template"]["spec"]["containers"][0]["env"]
        assert env[0]["name"] == "ALLOWED_DOMAINS"
        assert env[0]["value"] == ".googleapis.com,.pypi.org"

    def test_update_domains_with_single_domain(self) -> None:
        """Updating with a single domain works correctly."""
        oc = MagicMock(spec=OcClient)
        oc.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        pm = ProxyManager(oc, "test-ns")
        pm.update_deployment_domains("my-session", [".example.com"])

        args = oc.run.call_args[0]
        patch_arg = [a for a in args if a.startswith("-p=")]
        patch_data = json.loads(patch_arg[0][3:])
        env = patch_data["spec"]["template"]["spec"]["containers"][0]["env"]
        assert env[0]["value"] == ".example.com"

    def test_update_domains_with_empty_list(self) -> None:
        """Updating with an empty list sets ALLOWED_DOMAINS to empty."""
        oc = MagicMock(spec=OcClient)
        oc.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        pm = ProxyManager(oc, "test-ns")
        pm.update_deployment_domains("my-session", [])

        args = oc.run.call_args[0]
        patch_arg = [a for a in args if a.startswith("-p=")]
        patch_data = json.loads(patch_arg[0][3:])
        env = patch_data["spec"]["template"]["spec"]["containers"][0]["env"]
        assert env[0]["value"] == ""


# ---------------------------------------------------------------------------
# OpenShiftBackend: get_allowed_domains
# ---------------------------------------------------------------------------


class TestOpenShiftGetAllowedDomains:
    """Tests for OpenShiftBackend.get_allowed_domains method."""

    @patch("subprocess.run")
    def test_returns_none_when_no_proxy_deployment(self, mock_run: MagicMock) -> None:
        """get_allowed_domains returns None when no proxy deployment exists (unrestricted)."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # First call: get statefulset (session exists)
        sts_response = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "metadata": {
                        "name": "paude-my-session",
                        "labels": {"paude.io/session-name": "my-session"},
                        "annotations": {},
                    },
                    "spec": {"replicas": 1},
                    "status": {"readyReplicas": 1},
                }
            ),
            stderr="",
        )
        # Second call: get proxy deployment (not found)
        proxy_not_found = MagicMock(
            returncode=1,
            stdout="",
            stderr="not found",
        )

        mock_run.side_effect = [sts_response, proxy_not_found]

        result = backend.get_allowed_domains("my-session")

        assert result is None

    @patch("subprocess.run")
    def test_returns_domain_list_when_proxy_exists(self, mock_run: MagicMock) -> None:
        """get_allowed_domains returns domain list when proxy deployment exists."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # First call: get statefulset (session exists)
        sts_response = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "metadata": {
                        "name": "paude-my-session",
                        "labels": {"paude.io/session-name": "my-session"},
                        "annotations": {},
                    },
                    "spec": {"replicas": 1},
                    "status": {"readyReplicas": 1},
                }
            ),
            stderr="",
        )
        # Second call: get proxy deployment (exists)
        proxy_found = MagicMock(
            returncode=0,
            stdout="deployment found",
            stderr="",
        )
        # Third call: get ALLOWED_DOMAINS from deployment env
        domains_response = MagicMock(
            returncode=0,
            stdout=".googleapis.com,.pypi.org",
            stderr="",
        )

        mock_run.side_effect = [sts_response, proxy_found, domains_response]

        result = backend.get_allowed_domains("my-session")

        assert result is not None
        assert ".googleapis.com" in result
        assert ".pypi.org" in result

    @patch("subprocess.run")
    def test_raises_session_not_found_error(self, mock_run: MagicMock) -> None:
        """get_allowed_domains raises SessionNotFoundError when session doesn't exist."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # get statefulset returns not found
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="not found",
        )

        with pytest.raises(SessionNotFoundError):
            backend.get_allowed_domains("nonexistent-session")


# ---------------------------------------------------------------------------
# OpenShiftBackend: update_allowed_domains
# ---------------------------------------------------------------------------


class TestOpenShiftUpdateAllowedDomains:
    """Tests for OpenShiftBackend.update_allowed_domains method."""

    @patch("subprocess.run")
    def test_delegates_to_proxy_manager(self, mock_run: MagicMock) -> None:
        """update_allowed_domains delegates domain update to ProxyManager."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # First call: get statefulset (session exists)
        sts_response = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "metadata": {
                        "name": "paude-my-session",
                        "labels": {"paude.io/session-name": "my-session"},
                        "annotations": {},
                    },
                    "spec": {"replicas": 1},
                    "status": {"readyReplicas": 1},
                }
            ),
            stderr="",
        )
        # Second call: get proxy deployment (exists)
        proxy_found = MagicMock(returncode=0, stdout="", stderr="")
        # Third call: oc patch (the actual update)
        patch_response = MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = [sts_response, proxy_found, patch_response]

        backend.update_allowed_domains(
            "my-session", [".googleapis.com", ".example.com"]
        )

        # Should have called oc patch on the proxy deployment
        patch_calls = [c for c in mock_run.call_args_list if "patch" in str(c)]
        assert len(patch_calls) >= 1

    @patch("subprocess.run")
    def test_raises_session_not_found_error(self, mock_run: MagicMock) -> None:
        """update_allowed_domains raises SessionNotFoundError when session doesn't exist."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="not found",
        )

        with pytest.raises(SessionNotFoundError):
            backend.update_allowed_domains("nonexistent", [".example.com"])


# ---------------------------------------------------------------------------
# PodmanBackend: get_allowed_domains
# ---------------------------------------------------------------------------


class TestPodmanGetAllowedDomains:
    """Tests for PodmanBackend.get_allowed_domains method."""

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_none_when_no_proxy(self, mock_runner_class: MagicMock) -> None:
        """get_allowed_domains returns None when no proxy exists (unrestricted)."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_allowed_domains("my-session")
        assert result is None

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_domain_list_when_proxy_exists(
        self, mock_runner_class: MagicMock
    ) -> None:
        """get_allowed_domains returns domains from proxy container env."""
        mock_runner = MagicMock()
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_env.return_value = ".googleapis.com,.pypi.org"
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_allowed_domains("my-session")

        assert result == [".googleapis.com", ".pypi.org"]
        mock_runner.get_container_env.assert_called_once_with(
            "paude-proxy-my-session", "ALLOWED_DOMAINS"
        )

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_returns_empty_list_when_proxy_has_no_domains(
        self, mock_runner_class: MagicMock
    ) -> None:
        """get_allowed_domains returns empty list when ALLOWED_DOMAINS is empty."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_env.return_value = ""
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        result = backend.get_allowed_domains("my-session")
        assert result == []

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_raises_session_not_found(self, mock_runner_class: MagicMock) -> None:
        """get_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        with pytest.raises(PodmanSessionNotFoundError):
            backend.get_allowed_domains("nonexistent")


# ---------------------------------------------------------------------------
# PodmanBackend: update_allowed_domains
# ---------------------------------------------------------------------------


class TestPodmanUpdateAllowedDomains:
    """Tests for PodmanBackend.update_allowed_domains method."""

    @patch("paude.backends.podman.proxy.get_podman_machine_dns")
    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_recreates_proxy_with_new_domains(
        self,
        mock_runner_class: MagicMock,
        mock_dns: MagicMock,
    ) -> None:
        """update_allowed_domains recreates proxy with new domain list."""
        mock_runner = MagicMock()
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_image.return_value = "proxy:latest"
        mock_runner_class.return_value = mock_runner
        mock_dns.return_value = None

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner
        backend._network_manager = MagicMock()

        backend.update_allowed_domains(
            "my-session", [".googleapis.com", ".example.com"]
        )

        mock_runner.recreate_session_proxy.assert_called_once_with(
            name="paude-proxy-my-session",
            image="proxy:latest",
            network="paude-net-my-session",
            dns=None,
            allowed_domains=[".googleapis.com", ".example.com"],
        )

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_raises_session_not_found(self, mock_runner_class: MagicMock) -> None:
        """update_allowed_domains raises SessionNotFoundError when session missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        with pytest.raises(PodmanSessionNotFoundError):
            backend.update_allowed_domains("nonexistent", [".example.com"])

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_raises_value_error_when_no_proxy(
        self, mock_runner_class: MagicMock
    ) -> None:
        """update_allowed_domains raises ValueError when session has no proxy."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        with pytest.raises(ValueError, match="no proxy"):
            backend.update_allowed_domains("my-session", [".example.com"])
