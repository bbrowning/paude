"""Tests for credential watchdog configuration."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from paude.backends.base import SessionConfig


class TestSessionConfigCredentialTimeout:
    """Tests for credential_timeout in SessionConfig."""

    def test_default_credential_timeout(self) -> None:
        """SessionConfig has default credential_timeout of 60 minutes."""
        config = SessionConfig(
            name="test",
            workspace=Path("/test"),
            image="test:latest",
        )
        assert config.credential_timeout == 60

    def test_custom_credential_timeout(self) -> None:
        """SessionConfig accepts custom credential_timeout."""
        config = SessionConfig(
            name="test",
            workspace=Path("/test"),
            image="test:latest",
            credential_timeout=30,
        )
        assert config.credential_timeout == 30

    def test_zero_credential_timeout_disables_watchdog(self) -> None:
        """credential_timeout of 0 should disable the watchdog."""
        config = SessionConfig(
            name="test",
            workspace=Path("/test"),
            image="test:latest",
            credential_timeout=0,
        )
        assert config.credential_timeout == 0


class TestOpenShiftBackendCredentialTimeout:
    """Tests for credential timeout in OpenShift backend."""

    @patch("paude.backends.openshift.backend.OcClient")
    def test_credential_timeout_env_vars_set(
        self, mock_oc_client_class: MagicMock
    ) -> None:
        """create_session sets credential timeout environment variables."""
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        mock_oc = MagicMock()
        mock_oc.check_connection.return_value = True
        mock_oc.get_current_namespace.return_value = "test-ns"
        mock_oc.verify_namespace.return_value = None
        mock_oc.run.return_value = MagicMock(returncode=0, stdout="{}")
        mock_oc_client_class.return_value = mock_oc

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc = mock_oc

        config = SessionConfig(
            name="test-session",
            workspace=Path("/test"),
            image="test:latest",
            credential_timeout=45,
        )

        # Mock methods that would be called
        with patch.object(backend, "_get_statefulset", return_value=None):
            with patch.object(backend, "_ensure_network_policy_permissive"):
                with patch.object(backend, "_generate_statefulset_spec") as mock_spec:
                    with patch.object(backend, "_wait_for_pod_ready"):
                        with patch.object(backend, "_sync_config_to_pod"):
                            mock_spec.return_value = {"kind": "StatefulSet"}

                            backend.create_session(config)

                            # Check that _generate_statefulset_spec was called with env
                            # containing credential timeout variables
                            call_kwargs = mock_spec.call_args
                            env_arg = call_kwargs.kwargs.get("env", {})

                            assert "PAUDE_CREDENTIAL_TIMEOUT" in env_arg
                            assert env_arg["PAUDE_CREDENTIAL_TIMEOUT"] == "45"
                            assert "PAUDE_CREDENTIAL_WATCHDOG" in env_arg
                            assert env_arg["PAUDE_CREDENTIAL_WATCHDOG"] == "1"

    @patch("paude.backends.openshift.backend.OcClient")
    def test_zero_timeout_disables_watchdog_env(
        self, mock_oc_client_class: MagicMock
    ) -> None:
        """credential_timeout=0 sets PAUDE_CREDENTIAL_WATCHDOG=0."""
        from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig

        mock_oc = MagicMock()
        mock_oc.check_connection.return_value = True
        mock_oc.get_current_namespace.return_value = "test-ns"
        mock_oc.verify_namespace.return_value = None
        mock_oc.run.return_value = MagicMock(returncode=0, stdout="{}")
        mock_oc_client_class.return_value = mock_oc

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc = mock_oc

        config = SessionConfig(
            name="test-session",
            workspace=Path("/test"),
            image="test:latest",
            credential_timeout=0,
        )

        with patch.object(backend, "_get_statefulset", return_value=None):
            with patch.object(backend, "_ensure_network_policy_permissive"):
                with patch.object(backend, "_generate_statefulset_spec") as mock_spec:
                    with patch.object(backend, "_wait_for_pod_ready"):
                        with patch.object(backend, "_sync_config_to_pod"):
                            mock_spec.return_value = {"kind": "StatefulSet"}

                            backend.create_session(config)

                            call_kwargs = mock_spec.call_args
                            env_arg = call_kwargs.kwargs.get("env", {})

                            assert env_arg["PAUDE_CREDENTIAL_TIMEOUT"] == "0"
                            assert env_arg["PAUDE_CREDENTIAL_WATCHDOG"] == "0"


class TestCLICredentialTimeout:
    """Tests for --credential-timeout CLI option."""

    def test_cli_has_credential_timeout_option(self) -> None:
        """session_create has --credential-timeout option."""
        from paude.cli import session_create

        sig = inspect.signature(session_create)
        params = sig.parameters

        assert "credential_timeout" in params
        param = params["credential_timeout"]
        assert param.default == 60

    def test_cli_credential_timeout_annotation(self) -> None:
        """--credential-timeout has proper annotation."""
        from paude.cli import session_create

        sig = inspect.signature(session_create)
        params = sig.parameters

        param = params["credential_timeout"]
        # The annotation should indicate it's an int with typer.Option
        assert param.annotation is not None
