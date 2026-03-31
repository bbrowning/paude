"""Tests for the OpenShift backend module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.openshift import (
    OcNotInstalledError,
    OcNotLoggedInError,
    OcTimeoutError,
    OpenShiftBackend,
    OpenShiftConfig,
)


class TestOpenShiftConfig:
    """Tests for OpenShiftConfig dataclass."""

    def test_default_values(self) -> None:
        """OpenShiftConfig has sensible defaults."""
        config = OpenShiftConfig()

        assert config.context is None
        assert config.namespace is None  # None means use current context namespace
        assert "requests" in config.resources
        assert "limits" in config.resources
        assert "requests" in config.build_resources
        assert "limits" in config.build_resources

    def test_custom_values(self) -> None:
        """OpenShiftConfig accepts custom values."""
        config = OpenShiftConfig(
            context="my-context",
            namespace="my-namespace",
            resources={"requests": {"cpu": "2", "memory": "8Gi"}},
            build_resources={"requests": {"cpu": "1", "memory": "4Gi"}},
        )

        assert config.context == "my-context"
        assert config.namespace == "my-namespace"
        assert config.resources["requests"]["cpu"] == "2"
        assert config.build_resources["requests"]["memory"] == "4Gi"


class TestOpenShiftBackend:
    """Tests for OpenShiftBackend class."""

    def test_instantiation(self) -> None:
        """OpenShiftBackend can be instantiated."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        assert backend is not None

    def test_instantiation_with_config(self) -> None:
        """OpenShiftBackend accepts config."""
        config = OpenShiftConfig(namespace="test")
        backend = OpenShiftBackend(config=config)
        assert backend._config.namespace == "test"


class TestRunOc:
    """Tests for _oc.run method."""

    @patch("subprocess.run")
    def test_run_oc_builds_command(self, mock_run: MagicMock) -> None:
        """_oc.run builds correct command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc.run("get", "pods")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["oc", "get", "pods"]

    @patch("subprocess.run")
    def test_run_oc_includes_context(self, mock_run: MagicMock) -> None:
        """_oc.run includes context when specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = OpenShiftConfig(context="my-context")
        backend = OpenShiftBackend(config=config)
        backend._oc.run("get", "pods")

        args = mock_run.call_args[0][0]
        assert args == ["oc", "--context", "my-context", "get", "pods"]

    @patch("subprocess.run")
    def test_run_oc_raises_on_not_installed(self, mock_run: MagicMock) -> None:
        """_oc.run raises OcNotInstalledError when oc not found."""
        mock_run.side_effect = FileNotFoundError()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotInstalledError):
            backend._oc.run("version")

    @patch("subprocess.run")
    def test_run_oc_raises_on_not_logged_in(self, mock_run: MagicMock) -> None:
        """_oc.run raises OcNotLoggedInError when not logged in."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: You must be logged in to the server",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotLoggedInError):
            backend._oc.run("whoami")

    @patch("subprocess.run")
    def test_run_oc_passes_input(self, mock_run: MagicMock) -> None:
        """_oc.run passes input data to subprocess."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc.run("apply", "-f", "-", input_data='{"kind":"Pod"}')

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["input"] == '{"kind":"Pod"}'

    def test_timeout_constants_have_expected_values(self) -> None:
        """Timeout constants are set to expected values."""
        # OC_DEFAULT_TIMEOUT: standard commands should complete quickly
        assert OpenShiftBackend.OC_DEFAULT_TIMEOUT == 30

        # OC_EXEC_TIMEOUT: exec operations may be slow after pod restart
        assert OpenShiftBackend.OC_EXEC_TIMEOUT == 120

    @patch("subprocess.run")
    def test_run_oc_uses_default_timeout(self, mock_run: MagicMock) -> None:
        """_oc.run uses default timeout when none specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc.run("get", "pods")

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] == OpenShiftBackend.OC_DEFAULT_TIMEOUT

    @patch("subprocess.run")
    def test_run_oc_uses_custom_timeout(self, mock_run: MagicMock) -> None:
        """_oc.run uses custom timeout when specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc.run("get", "pods", timeout=60)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] == 60

    @patch("subprocess.run")
    def test_run_oc_no_timeout_when_zero(self, mock_run: MagicMock) -> None:
        """_oc.run disables timeout when 0 is specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._oc.run("get", "pods", timeout=0)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] is None

    @patch("subprocess.run")
    def test_run_oc_raises_on_timeout(self, mock_run: MagicMock) -> None:
        """_oc.run raises OcTimeoutError when command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["oc", "get", "pods"], timeout=30
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcTimeoutError) as exc_info:
            backend._oc.run("get", "pods")

        assert "timed out" in str(exc_info.value)
        assert "oc get pods" in str(exc_info.value)
        assert "network issues" in str(exc_info.value)


class TestCheckConnection:
    """Tests for _oc.check_connection method."""

    @patch("subprocess.run")
    def test_returns_true_when_logged_in(self, mock_run: MagicMock) -> None:
        """_oc.check_connection returns True when logged in."""
        mock_run.return_value = MagicMock(returncode=0, stdout="user", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._oc.check_connection()

        assert result is True

    @patch("subprocess.run")
    def test_raises_when_not_logged_in(self, mock_run: MagicMock) -> None:
        """_oc.check_connection raises when not logged in."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotLoggedInError):
            backend._oc.check_connection()


class TestListSessions:
    """Tests for list_sessions method."""

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run: MagicMock) -> None:
        """list_sessions returns empty list on error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        sessions = backend.list_sessions()

        assert sessions == []


# =============================================================================
# Session Management Tests (New Backend Protocol)
# =============================================================================


class TestOpenShiftSessionHelpers:
    """Tests for OpenShift session helper functions."""

    def test_generate_session_name_includes_project(self) -> None:
        """Session name includes project name from workspace."""
        from paude.backends.openshift import _generate_session_name

        name = _generate_session_name(Path("/home/user/my-project"))
        assert name.startswith("my-project-")

    def test_encode_decode_path_roundtrip(self) -> None:
        """Path encoding and decoding is reversible."""
        from paude.backends.openshift import _decode_path, _encode_path

        original = Path("/home/user/my project/src")
        encoded = _encode_path(original)
        decoded = _decode_path(encoded)
        assert decoded == original


class TestOpenShiftCreateSession:
    """Tests for OpenShiftBackend.create_session."""

    @patch("subprocess.run")
    def test_create_session_creates_statefulset(self, mock_run: MagicMock) -> None:
        """Create session creates a StatefulSet."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        session = backend.create_session(config)

        assert session.name == "test-session"
        assert session.status == "running"
        assert session.backend_type == "openshift"
        assert session.container_id == "paude-test-session-0"
        assert session.volume_name == "workspace-paude-test-session-0"

        # Verify oc apply was called for StatefulSet
        calls = mock_run.call_args_list
        apply_calls = [c for c in calls if "apply" in str(c)]
        assert len(apply_calls) > 0

    @patch("subprocess.run")
    def test_create_session_raises_if_exists(self, mock_run: MagicMock) -> None:
        """Create session raises SessionExistsError if session exists."""

        # First call to get statefulset returns existing
        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-existing"},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig
        from paude.backends.openshift import SessionExistsError

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="existing",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        with pytest.raises(SessionExistsError):
            backend.create_session(config)

    @patch("subprocess.run")
    def test_create_session_waits_for_pod_and_syncs_config(
        self, mock_run: MagicMock
    ) -> None:
        """Create session waits for pod ready and syncs config."""
        calls_log = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            calls_log.append(cmd)
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        session = backend.create_session(config)

        # Verify pod status check was called (waiting for pod ready)
        pod_status_calls = [
            c for c in calls_log if "get" in c and "pod" in c and "jsonpath" in str(c)
        ]
        assert len(pod_status_calls) >= 1, "Should check pod status"

        # Verify sync was called (exec mkdir for config directory)
        sync_calls = [c for c in calls_log if "exec" in c and "mkdir" in str(c)]
        assert len(sync_calls) >= 1, "Should sync config to pod"

        # Verify session is returned as running
        assert session.status == "running"


class TestOpenShiftDeleteSession:
    """Tests for OpenShiftBackend.delete_session."""

    @patch("subprocess.run")
    def test_delete_session_requires_confirmation(self, mock_run: MagicMock) -> None:
        """Delete session requires confirm=True."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(ValueError, match="(?i)confirm"):
            backend.delete_session("my-session", confirm=False)

    @patch("subprocess.run")
    def test_delete_session_raises_if_not_found(self, mock_run: MagicMock) -> None:
        """Delete session raises SessionNotFoundError if not found."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                # Not found - return non-zero exit code with empty stdout
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.openshift import SessionNotFoundError

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(SessionNotFoundError):
            backend.delete_session("nonexistent", confirm=True)

    @patch("subprocess.run")
    def test_delete_session_deletes_resources(self, mock_run: MagicMock) -> None:
        """Delete session deletes StatefulSet, PVC, and credentials."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.delete_session("test", confirm=True)

        # Verify delete commands were called
        calls = mock_run.call_args_list
        delete_calls = [c for c in calls if "delete" in str(c)]
        assert len(delete_calls) >= 2  # StatefulSet, PVC, and credentials


class TestOpenShiftStartSession:
    """Tests for OpenShiftBackend.start_session."""

    @patch("subprocess.run")
    def test_start_session_raises_if_not_found(self, mock_run: MagicMock) -> None:
        """Start session raises SessionNotFoundError if not found."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                # Not found - return non-zero exit code
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.openshift import SessionNotFoundError

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(SessionNotFoundError):
            backend.start_session("nonexistent")

    @patch("subprocess.run")
    def test_start_session_scales_statefulset(self, mock_run: MagicMock) -> None:
        """Start session scales StatefulSet to 1."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {
                                "name": "paude-test",
                                "annotations": {
                                    "paude.io/workspace": "",
                                },
                            },
                            "spec": {"replicas": 0},
                        }
                    ),
                    stderr="",
                )
            # Proxy deployment doesn't exist (no proxy for this test)
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # For scale and other commands
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Mock wait_for_pod_ready and connect_session to avoid actual waits
        with patch.object(backend._pod_waiter, "wait_for_ready"):
            with patch.object(backend, "connect_session", return_value=0):
                exit_code = backend.start_session("test")

        assert exit_code == 0

        # Verify scale command was called
        calls = mock_run.call_args_list
        scale_calls = [c for c in calls if "scale" in str(c)]
        assert len(scale_calls) >= 1

        # Verify NO proxy scale command was issued (proxy doesn't exist)
        proxy_scale_calls = [
            c
            for c in calls
            if "scale" in str(c) and "deployment" in str(c) and "paude-proxy" in str(c)
        ]
        assert len(proxy_scale_calls) == 0

    @patch("subprocess.run")
    def test_start_session_scales_proxy_to_one(self, mock_run: MagicMock) -> None:
        """Start session scales proxy Deployment to 1 when it exists."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {
                                "name": "paude-test",
                                "annotations": {
                                    "paude.io/workspace": "",
                                },
                            },
                            "spec": {"replicas": 0},
                        }
                    ),
                    stderr="",
                )
            # Proxy deployment exists
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            # For scale and other commands
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._pod_waiter, "wait_for_ready"):
            with patch.object(backend._proxy, "wait_for_ready"):
                with patch.object(backend, "connect_session", return_value=0):
                    exit_code = backend.start_session("test")

        assert exit_code == 0

        # Verify proxy scale command was called with replicas=1
        calls = mock_run.call_args_list
        proxy_scale_calls = [
            c
            for c in calls
            if "scale" in str(c)
            and "deployment" in str(c)
            and "paude-proxy" in str(c)
            and "replicas=1" in str(c)
        ]
        assert len(proxy_scale_calls) == 1


class TestOpenShiftStopSession:
    """Tests for OpenShiftBackend.stop_session."""

    @patch("subprocess.run")
    def test_stop_session_scales_to_zero(self, mock_run: MagicMock) -> None:
        """Stop session scales StatefulSet to 0."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                            "spec": {"replicas": 1},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.stop_session("test")

        # Verify scale to 0 was called
        calls = mock_run.call_args_list
        scale_calls = [c for c in calls if "scale" in str(c) and "replicas=0" in str(c)]
        assert len(scale_calls) >= 1

    @patch("subprocess.run")
    def test_stop_session_raises_if_not_found(self, mock_run: MagicMock) -> None:
        """Stop session raises SessionNotFoundError if not found."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                # Not found - return non-zero exit code
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.openshift import SessionNotFoundError

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(SessionNotFoundError):
            backend.stop_session("nonexistent")

    @patch("subprocess.run")
    def test_stop_session_scales_proxy_to_zero(self, mock_run: MagicMock) -> None:
        """Stop session scales proxy Deployment to 0 when it exists."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                            "spec": {"replicas": 1},
                        }
                    ),
                    stderr="",
                )
            # Proxy deployment exists
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.stop_session("test")

        # Verify proxy scale to 0 was called
        calls = mock_run.call_args_list
        proxy_scale_calls = [
            c
            for c in calls
            if "scale" in str(c)
            and "deployment" in str(c)
            and "paude-proxy" in str(c)
            and "replicas=0" in str(c)
        ]
        assert len(proxy_scale_calls) == 1

    @patch("subprocess.run")
    def test_stop_session_succeeds_when_proxy_does_not_exist(
        self, mock_run: MagicMock
    ) -> None:
        """Stop session succeeds and skips proxy when it doesn't exist."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                            "spec": {"replicas": 1},
                        }
                    ),
                    stderr="",
                )
            # Proxy deployment doesn't exist
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr='Error: deployments.apps "paude-proxy-test" not found',
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Should not raise - proxy doesn't exist so we skip scaling it
        backend.stop_session("test")

        # Verify StatefulSet was still scaled to 0
        calls = mock_run.call_args_list
        sts_scale_calls = [
            c
            for c in calls
            if "scale" in str(c) and "statefulset" in str(c) and "replicas=0" in str(c)
        ]
        assert len(sts_scale_calls) == 1

        # Verify NO proxy scale was attempted (proxy doesn't exist)
        proxy_scale_calls = [
            c
            for c in calls
            if "scale" in str(c) and "deployment" in str(c) and "paude-proxy" in str(c)
        ]
        assert len(proxy_scale_calls) == 0


class TestOpenShiftListSessions:
    """Tests for OpenShiftBackend.list_sessions (new protocol)."""

    @patch("subprocess.run")
    def test_list_sessions_returns_statefulsets(self, mock_run: MagicMock) -> None:
        """List sessions returns StatefulSets as sessions."""
        from paude.backends.openshift import _encode_path

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulsets" in cmd and "-l" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "items": [
                                {
                                    "metadata": {
                                        "name": "paude-test-session",
                                        "labels": {
                                            "app": "paude",
                                            "paude.io/session-name": "test-session",
                                        },
                                        "annotations": {
                                            "paude.io/workspace": _encode_path(
                                                Path("/home/user/project")
                                            ),
                                            "paude.io/created-at": "2024-01-15T10:00:00Z",
                                        },
                                    },
                                    "spec": {"replicas": 1},
                                    "status": {"readyReplicas": 1},
                                }
                            ]
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].name == "test-session"
        assert sessions[0].status == "running"
        assert sessions[0].backend_type == "openshift"

    @patch("subprocess.run")
    def test_list_sessions_returns_empty_on_error(self, mock_run: MagicMock) -> None:
        """List sessions returns empty list on error."""

        def run_side_effect(*args, **kwargs):
            return MagicMock(returncode=1, stdout="", stderr="error")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        sessions = backend.list_sessions()

        assert sessions == []


class TestOpenShiftGetSession:
    """Tests for OpenShiftBackend.get_session."""

    @patch("subprocess.run")
    def test_get_session_returns_session_if_found(self, mock_run: MagicMock) -> None:
        """Get session returns session if StatefulSet found."""
        from paude.backends.openshift import _encode_path

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "metadata": {
                                "name": "paude-my-session",
                                "annotations": {
                                    "paude.io/workspace": _encode_path(
                                        Path("/home/user/project")
                                    ),
                                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                                },
                            },
                            "spec": {"replicas": 0},
                            "status": {},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        session = backend.get_session("my-session")

        assert session is not None
        assert session.name == "my-session"
        assert session.status == "stopped"

    @patch("subprocess.run")
    def test_get_session_returns_none_if_not_found(self, mock_run: MagicMock) -> None:
        """Get session returns None if StatefulSet not found."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                # Not found - return non-zero exit code
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        session = backend.get_session("nonexistent")

        assert session is None


class TestOpenShiftStatefulSetSpec:
    """Tests for _generate_statefulset_spec."""

    def test_generates_statefulset_with_volume_claim_templates(self) -> None:
        """StatefulSet spec includes volumeClaimTemplates."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test-session",
            image="paude:latest",
            env={},
            workspace=Path("/home/user/project"),
        )

        assert spec["kind"] == "StatefulSet"
        assert spec["metadata"]["name"] == "paude-test-session"
        assert spec["spec"]["replicas"] == 1  # Created running
        assert "volumeClaimTemplates" in spec["spec"]
        assert len(spec["spec"]["volumeClaimTemplates"]) > 0

    def test_statefulset_includes_workspace_annotation(self) -> None:
        """StatefulSet includes workspace path in annotations."""
        from paude.backends.openshift import _encode_path

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        workspace = Path("/home/user/my-project")
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=workspace,
        )

        annotations = spec["metadata"]["annotations"]
        assert annotations["paude.io/workspace"] == _encode_path(workspace)

    def test_statefulset_uses_custom_pvc_size(self) -> None:
        """StatefulSet uses custom PVC size when specified."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=Path("/project"),
            pvc_size="50Gi",
        )

        vct = spec["spec"]["volumeClaimTemplates"][0]
        assert vct["spec"]["resources"]["requests"]["storage"] == "50Gi"

    def test_statefulset_uses_custom_storage_class(self) -> None:
        """StatefulSet uses custom storage class when specified."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=Path("/project"),
            storage_class="fast-ssd",
        )

        vct = spec["spec"]["volumeClaimTemplates"][0]
        assert vct["spec"]["storageClassName"] == "fast-ssd"

    def test_statefulset_does_not_include_working_dir(self) -> None:
        """StatefulSet container spec must NOT include workingDir.

        If workingDir is set, kubelet creates the directory as root before
        the container starts, which causes permission errors for the random
        UID that OpenShift assigns. The entrypoint script creates the
        workspace directory with correct ownership instead.
        """
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=Path("/project"),
        )

        container = spec["spec"]["template"]["spec"]["containers"][0]
        assert "workingDir" not in container, (
            "workingDir must not be set - kubelet creates it as root"
        )

    def test_statefulset_includes_tmpfs_credentials_volume(self) -> None:
        """StatefulSet includes tmpfs emptyDir volume for credentials.

        Credentials are stored in RAM-only tmpfs to prevent persistence
        to disk. This ensures credentials are automatically cleared when
        the pod stops/restarts.
        """
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=Path("/project"),
        )

        # Check volumes include credentials tmpfs
        volumes = spec["spec"]["template"]["spec"]["volumes"]
        creds_volume = next((v for v in volumes if v["name"] == "credentials"), None)
        assert creds_volume is not None, "Should have credentials volume"
        assert "emptyDir" in creds_volume, "Should be emptyDir volume"
        assert creds_volume["emptyDir"]["medium"] == "Memory", "Should be tmpfs"
        assert creds_volume["emptyDir"]["sizeLimit"] == "100Mi"

        # Check volume mounts include /credentials
        container = spec["spec"]["template"]["spec"]["containers"][0]
        volume_mounts = container["volumeMounts"]
        creds_mount = next(
            (m for m in volume_mounts if m["name"] == "credentials"), None
        )
        assert creds_mount is not None, "Should have credentials mount"
        assert creds_mount["mountPath"] == "/credentials"

    def test_statefulset_image_pull_policy_defaults_to_always(self) -> None:
        """StatefulSet defaults imagePullPolicy to Always."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_statefulset_spec(
            session_name="test",
            image="paude:latest",
            env={},
            workspace=Path("/project"),
        )

        container = spec["spec"]["template"]["spec"]["containers"][0]
        assert container["imagePullPolicy"] == "Always"

    def test_statefulset_image_pull_policy_from_env(self) -> None:
        """StatefulSet uses imagePullPolicy from PAUDE_IMAGE_PULL_POLICY env var."""
        import os

        original = os.environ.get("PAUDE_IMAGE_PULL_POLICY")
        try:
            os.environ["PAUDE_IMAGE_PULL_POLICY"] = "IfNotPresent"

            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            spec = backend._generate_statefulset_spec(
                session_name="test",
                image="paude:latest",
                env={},
                workspace=Path("/project"),
            )

            container = spec["spec"]["template"]["spec"]["containers"][0]
            assert container["imagePullPolicy"] == "IfNotPresent"
        finally:
            if original is None:
                os.environ.pop("PAUDE_IMAGE_PULL_POLICY", None)
            else:
                os.environ["PAUDE_IMAGE_PULL_POLICY"] = original

    def test_statefulset_image_pull_policy_never(self) -> None:
        """StatefulSet can use Never imagePullPolicy for local images."""
        import os

        original = os.environ.get("PAUDE_IMAGE_PULL_POLICY")
        try:
            os.environ["PAUDE_IMAGE_PULL_POLICY"] = "Never"

            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            spec = backend._generate_statefulset_spec(
                session_name="test",
                image="my-local-image:latest",
                env={},
                workspace=Path("/project"),
            )

            container = spec["spec"]["template"]["spec"]["containers"][0]
            assert container["imagePullPolicy"] == "Never"
        finally:
            if original is None:
                os.environ.pop("PAUDE_IMAGE_PULL_POLICY", None)
            else:
                os.environ["PAUDE_IMAGE_PULL_POLICY"] = original


class TestBuildFailedError:
    """Tests for BuildFailedError exception."""

    def test_message_format(self) -> None:
        """BuildFailedError has expected message format."""
        from paude.backends.openshift import BuildFailedError

        error = BuildFailedError("paude-abc123-1", "OutOfMemory")
        assert "paude-abc123-1" in str(error)
        assert "OutOfMemory" in str(error)

    def test_includes_logs_when_provided(self) -> None:
        """BuildFailedError includes logs when provided."""
        from paude.backends.openshift import BuildFailedError

        logs = "Step 5/10: npm install\nOOM killed"
        error = BuildFailedError("paude-abc123-1", "OutOfMemory", logs=logs)
        assert "OOM killed" in str(error)


class TestSessionConnectorBuildExecCmd:
    """Tests for SessionConnector._build_exec_cmd port URL injection."""

    def _make_connector(self, context: str | None = None) -> Any:
        from paude.backends.openshift.config import OpenShiftConfig
        from paude.backends.openshift.session_connection import SessionConnector

        oc = MagicMock()
        config = OpenShiftConfig(context=context)
        return SessionConnector(
            oc=oc,
            namespace="test-ns",
            config=config,
            lookup=MagicMock(),
            syncer=MagicMock(),
        )

    def test_exec_cmd_without_port_urls(self) -> None:
        """Exec command has no env prefix when no port URLs."""
        connector = self._make_connector()
        cmd = connector._build_exec_cmd("pod-0", "test-ns")
        assert cmd == [
            "oc",
            "exec",
            "-it",
            "-n",
            "test-ns",
            "pod-0",
            "--",
            "/usr/local/bin/entrypoint-session.sh",
        ]

    def test_exec_cmd_with_port_urls(self) -> None:
        """Exec command includes env PAUDE_PORT_URLS when port URLs provided."""
        connector = self._make_connector()
        cmd = connector._build_exec_cmd(
            "pod-0",
            "test-ns",
            port_urls=["http://localhost:18789"],
        )
        assert "env" in cmd
        assert "PAUDE_PORT_URLS=http://localhost:18789" in cmd
        # env and PAUDE_PORT_URLS must come before entrypoint
        env_idx = cmd.index("env")
        ep_idx = cmd.index("/usr/local/bin/entrypoint-session.sh")
        assert env_idx < ep_idx

    def test_exec_cmd_with_multiple_port_urls(self) -> None:
        """Multiple port URLs are semicolon-delimited."""
        connector = self._make_connector()
        cmd = connector._build_exec_cmd(
            "pod-0",
            "test-ns",
            port_urls=["http://localhost:8080", "http://localhost:8443"],
        )
        env_val = [c for c in cmd if c.startswith("PAUDE_PORT_URLS=")][0]
        assert env_val == (
            "PAUDE_PORT_URLS=http://localhost:8080;http://localhost:8443"
        )

    def test_exec_cmd_with_context_and_port_urls(self) -> None:
        """Context and port URLs work together."""
        connector = self._make_connector(context="my-ctx")
        cmd = connector._build_exec_cmd(
            "pod-0",
            "test-ns",
            port_urls=["http://localhost:18789"],
        )
        assert cmd[:4] == ["oc", "--context", "my-ctx", "exec"]
        assert "env" in cmd
        assert "PAUDE_PORT_URLS=http://localhost:18789" in cmd


class TestCreateBuildConfig:
    """Tests for _builder.create_build_config method."""

    @patch("subprocess.run")
    def test_creates_buildconfig_and_imagestream(self, mock_run: MagicMock) -> None:
        """_builder.create_build_config creates BuildConfig and ImageStream."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "buildconfig" in cmd:
                # BuildConfig doesn't exist yet
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # Apply commands succeed
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.create_build_config("abc123")

        # Should have called oc apply twice (ImageStream and BuildConfig)
        calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(calls) >= 2

    @patch("subprocess.run")
    def test_skips_if_buildconfig_exists(self, mock_run: MagicMock) -> None:
        """_builder.create_build_config skips if BuildConfig already exists."""
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.create_build_config("abc123")

        # Should only have called get, not apply
        calls = mock_run.call_args_list
        get_calls = [c for c in calls if "get" in str(c) and "buildconfig" in str(c)]
        apply_calls = [c for c in calls if "apply" in str(c)]
        assert len(get_calls) == 1
        assert len(apply_calls) == 0


class TestStartBinaryBuild:
    """Tests for _builder.start_binary_build method."""

    @patch("subprocess.run")
    def test_starts_build_with_from_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_builder.start_binary_build uses --from-dir option."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        build_name = backend._builder.start_binary_build("abc123", tmp_path)

        assert "paude-abc123-1" in build_name

        # Verify start-build was called with --from-dir
        calls = mock_run.call_args_list
        start_calls = [c for c in calls if "start-build" in str(c)]
        assert len(start_calls) >= 1
        cmd = start_calls[0][0][0]
        assert any("--from-dir" in str(arg) for arg in cmd)

    @patch("subprocess.run")
    def test_labels_build_with_session_name(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_builder.start_binary_build labels build when session_name provided."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.start_binary_build(
            "abc123", tmp_path, session_name="my-session"
        )

        # Verify label command was called (look for "label" as a command, not substring)
        calls = mock_run.call_args_list
        label_calls = [
            c
            for c in calls
            if len(c[0]) > 0 and "label" in c[0][0] and "start-build" not in str(c)
        ]
        assert len(label_calls) >= 1

        # Check the label command
        label_cmd = label_calls[0][0][0]
        assert "label" in label_cmd
        assert "build" in label_cmd
        assert any("paude.io/session-name=my-session" in str(arg) for arg in label_cmd)

    @patch("subprocess.run")
    def test_does_not_label_when_session_name_is_none(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_builder.start_binary_build does not label when session_name is None."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.start_binary_build("abc123", tmp_path, session_name=None)

        # Verify no label command was called (look for "label" as a command, not substring)
        calls = mock_run.call_args_list
        label_calls = [
            c
            for c in calls
            if len(c[0]) > 0 and "label" in c[0][0] and "start-build" not in str(c)
        ]
        assert len(label_calls) == 0


class TestDeleteSessionBuilds:
    """Tests for _builder.delete_session_builds method."""

    @patch("subprocess.run")
    def test_deletes_builds_with_session_label(self, mock_run: MagicMock) -> None:
        """_builder.delete_session_builds deletes builds with session label."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.delete_session_builds("my-session")

        # Verify delete build command was called with correct label
        calls = mock_run.call_args_list
        delete_calls = [c for c in calls if "delete" in str(c) and "build" in str(c)]
        assert len(delete_calls) >= 1

        cmd = delete_calls[0][0][0]
        assert "delete" in cmd
        assert "build" in cmd
        assert any("-l" in str(arg) for arg in cmd)
        assert any("paude.io/session-name=my-session" in str(arg) for arg in cmd)


class TestDeleteSessionCallsDeleteBuilds:
    """Tests for delete_session calling _builder.delete_session_builds."""

    @patch("subprocess.run")
    def test_delete_session_calls_delete_session_builds(
        self, mock_run: MagicMock
    ) -> None:
        """delete_session calls _builder.delete_session_builds."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.delete_session("test", confirm=True)

        # Verify _delete_session_builds was called (indirectly via oc delete build)
        calls_str = str(mock_run.call_args_list)
        assert "delete" in calls_str
        assert "build" in calls_str
        assert "paude.io/session-name=test" in calls_str


class TestEnsureImageViaBuildPassesSessionName:
    """Tests for ensure_image_via_build passing session_name to start_binary_build."""

    @patch("subprocess.run")
    def test_passes_session_name_to_start_binary_build(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ensure_image_via_build passes session_name to start_binary_build."""
        # Mock imagestreamtag check to return "not found" (need to build)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Patch the builder's methods directly
        with patch.object(backend._builder, "start_binary_build") as mock_start_build:
            with patch.object(backend._builder, "wait_for_build"):
                with patch.object(
                    backend._builder, "get_imagestream_reference"
                ) as mock_get_ref:
                    with patch.object(backend._builder, "create_build_config"):
                        mock_start_build.return_value = "paude-abc123-1"
                        mock_get_ref.return_value = (
                            "image-registry.svc:5000/ns/paude-abc123:latest"
                        )

                        backend.ensure_image_via_build(
                            config=None,
                            workspace=tmp_path,
                            session_name="my-session",
                        )

                        # Verify start_binary_build was called with session_name
                        mock_start_build.assert_called_once()
                        call_kwargs = mock_start_build.call_args.kwargs
                        assert call_kwargs.get("session_name") == "my-session"

    @patch("subprocess.run")
    def test_passes_none_when_session_name_not_provided(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ensure_image_via_build passes None when session_name not provided."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Patch the builder's methods directly
        with patch.object(backend._builder, "start_binary_build") as mock_start_build:
            with patch.object(backend._builder, "wait_for_build"):
                with patch.object(
                    backend._builder, "get_imagestream_reference"
                ) as mock_get_ref:
                    with patch.object(backend._builder, "create_build_config"):
                        mock_start_build.return_value = "paude-abc123-1"
                        mock_get_ref.return_value = (
                            "image-registry.svc:5000/ns/paude-abc123:latest"
                        )

                        backend.ensure_image_via_build(
                            config=None,
                            workspace=tmp_path,
                            # session_name not provided - should default to None
                        )

                        # Verify start_binary_build was called with None
                        mock_start_build.assert_called_once()
                        call_kwargs = mock_start_build.call_args.kwargs
                        assert call_kwargs.get("session_name") is None

    @patch("subprocess.run")
    def test_does_not_call_start_binary_build_when_image_exists(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ensure_image_via_build skips build when image already exists."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else []
            # Return success for imagestreamtag check (image exists)
            if "get" in cmd and "imagestreamtag" in cmd:
                return MagicMock(returncode=0, stdout="found", stderr="")
            # Return internal registry reference
            if "get" in cmd and "imagestream" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="image-registry.openshift-image-registry.svc:5000/ns/img",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.ensure_image_via_build(
            config=None,
            workspace=tmp_path,
            session_name="my-session",
        )

        # Verify start-build was NOT called (image exists, reused)
        calls_str = str(mock_run.call_args_list)
        assert "start-build" not in calls_str


class TestGetImagestreamReference:
    """Tests for _builder.get_imagestream_reference method."""

    @patch("subprocess.run")
    def test_returns_internal_reference(self, mock_run: MagicMock) -> None:
        """_builder.get_imagestream_reference returns internal image URL."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="image-registry.openshift-image-registry.svc:5000/test-ns/paude-abc123",
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ref = backend._builder.get_imagestream_reference("abc123")

        assert "image-registry.openshift-image-registry.svc:5000" in ref
        assert "paude-abc123" in ref
        assert ":latest" in ref

    @patch("subprocess.run")
    def test_falls_back_to_default_registry(self, mock_run: MagicMock) -> None:
        """_builder.get_imagestream_reference uses default when no dockerImageRepository."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ref = backend._builder.get_imagestream_reference("abc123")

        assert "image-registry.openshift-image-registry.svc:5000" in ref
        assert "test-ns" in ref
        assert "paude-abc123" in ref


# =============================================================================
# Proxy Pod Deployment Tests
# =============================================================================


class TestCreateProxyDeployment:
    """Tests for _proxy.create_deployment method."""

    @patch("subprocess.run")
    def test_creates_deployment_with_correct_spec(self, mock_run: MagicMock) -> None:
        """_proxy.create_deployment creates Deployment with correct spec."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.create_deployment("my-session", "quay.io/test/proxy:latest")

        # Find the apply call
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        # Check the deployment spec from input_data
        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        assert spec["kind"] == "Deployment"
        assert spec["metadata"]["name"] == "paude-proxy-my-session"
        assert spec["metadata"]["labels"]["app"] == "paude-proxy"
        assert spec["metadata"]["labels"]["paude.io/session-name"] == "my-session"
        assert spec["spec"]["replicas"] == 1

        pod_spec = spec["spec"]["template"]["spec"]
        assert pod_spec["automountServiceAccountToken"] is False
        assert pod_spec["enableServiceLinks"] is False

        container = pod_spec["containers"][0]
        assert container["name"] == "proxy"
        assert container["image"] == "quay.io/test/proxy:latest"
        assert container["ports"][0]["containerPort"] == 3128


class TestProxyImagePullPolicy:
    """Tests for proxy deployment imagePullPolicy from env var."""

    @patch("subprocess.run")
    def test_proxy_default_image_pull_policy(self, mock_run: MagicMock) -> None:
        """Proxy deployment defaults to Always imagePullPolicy."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.create_deployment("my-session", "quay.io/test/proxy:latest")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])
        container = spec["spec"]["template"]["spec"]["containers"][0]
        assert container["imagePullPolicy"] == "Always"

    @patch("subprocess.run")
    def test_proxy_image_pull_policy_from_env(self, mock_run: MagicMock) -> None:
        """Proxy deployment uses imagePullPolicy from PAUDE_IMAGE_PULL_POLICY env var."""
        import os

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        original = os.environ.get("PAUDE_IMAGE_PULL_POLICY")
        try:
            os.environ["PAUDE_IMAGE_PULL_POLICY"] = "IfNotPresent"

            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            backend._proxy.create_deployment("my-session", "quay.io/test/proxy:latest")

            apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
            assert len(apply_calls) >= 1

            call_kwargs = apply_calls[0][1]
            spec = json.loads(call_kwargs["input"])
            container = spec["spec"]["template"]["spec"]["containers"][0]
            assert container["imagePullPolicy"] == "IfNotPresent"
        finally:
            if original is None:
                os.environ.pop("PAUDE_IMAGE_PULL_POLICY", None)
            else:
                os.environ["PAUDE_IMAGE_PULL_POLICY"] = original


class TestCreateProxyService:
    """Tests for _proxy.create_service method."""

    @patch("subprocess.run")
    def test_creates_service_with_correct_spec(self, mock_run: MagicMock) -> None:
        """_proxy.create_service creates Service with correct spec."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        service_name = backend._proxy.create_service("my-session")

        assert service_name == "paude-proxy-my-session"

        # Find the apply call
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        assert spec["kind"] == "Service"
        assert spec["metadata"]["name"] == "paude-proxy-my-session"
        assert spec["metadata"]["labels"]["app"] == "paude-proxy"
        assert spec["spec"]["selector"]["app"] == "paude-proxy"
        assert spec["spec"]["selector"]["paude.io/session-name"] == "my-session"
        assert spec["spec"]["ports"][0]["port"] == 3128


class TestEnsureNetworkPolicyPermissive:
    """Tests for _proxy.ensure_network_policy_permissive method."""

    @patch("subprocess.run")
    def test_creates_permissive_egress_policy_for_paude_pod(
        self, mock_run: MagicMock
    ) -> None:
        """_proxy.ensure_network_policy_permissive creates policy allowing all egress."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.ensure_network_policy_permissive("my-session")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        assert spec["kind"] == "NetworkPolicy"
        assert spec["metadata"]["name"] == "paude-egress-my-session"
        assert spec["metadata"]["namespace"] == "test-ns"

        # Verify pod selector targets paude (not proxy)
        selector = spec["spec"]["podSelector"]["matchLabels"]
        assert selector["app"] == "paude"
        assert selector["paude.io/session-name"] == "my-session"

        # Verify egress allows all (empty rule)
        egress = spec["spec"]["egress"]
        assert len(egress) == 1
        assert egress[0] == {}  # Empty rule = allow all


class TestNetworkPolicyWithProxySelector:
    """Tests for NetworkPolicy using pod selector instead of CIDRs."""

    @patch("subprocess.run")
    def test_network_policy_uses_pod_selector(self, mock_run: MagicMock) -> None:
        """_proxy.ensure_network_policy uses pod selector for proxy access."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.ensure_network_policy("my-session")

        # Find the apply call
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        assert spec["kind"] == "NetworkPolicy"

        # Check egress rules
        egress = spec["spec"]["egress"]
        assert len(egress) == 2  # DNS and proxy access

        # First rule should be DNS (port 53) and mDNS (port 5353)
        dns_rule = egress[0]
        assert any(p["port"] == 53 for p in dns_rule["ports"])
        assert any(p["port"] == 5353 for p in dns_rule["ports"])

        # Second rule should use podSelector (not ipBlock/CIDRs)
        proxy_rule = egress[1]
        assert "to" in proxy_rule
        assert len(proxy_rule["to"]) == 1
        assert "podSelector" in proxy_rule["to"][0]
        selector = proxy_rule["to"][0]["podSelector"]
        assert selector["matchLabels"]["app"] == "paude-proxy"
        assert selector["matchLabels"]["paude.io/session-name"] == "my-session"
        assert proxy_rule["ports"][0]["port"] == 3128

    @patch("subprocess.run")
    def test_network_policy_no_cidr_blocks(self, mock_run: MagicMock) -> None:
        """_proxy.ensure_network_policy does not use CIDR blocks anymore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.ensure_network_policy("my-session")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        # Check that no ipBlock rules exist
        for rule in spec["spec"]["egress"]:
            if "to" in rule:
                for dest in rule["to"]:
                    assert "ipBlock" not in dest, "Should not use CIDR blocks"

    @patch("subprocess.run")
    def test_dns_rule_has_namespace_and_pod_selector(self, mock_run: MagicMock) -> None:
        """DNS rule uses both namespaceSelector AND podSelector for cross-namespace access.

        OpenShift DNS pods run in openshift-dns namespace. The NetworkPolicy must
        have BOTH namespaceSelector: {} AND podSelector: {} together in the same
        'to' object to correctly match "any pod in any namespace".

        Having just namespaceSelector: {} alone doesn't work in OVN-Kubernetes.
        """
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.ensure_network_policy("my-session")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        # DNS rule should have both namespaceSelector and podSelector
        dns_rule = spec["spec"]["egress"][0]
        assert "to" in dns_rule, "DNS rule should have 'to' selector"
        assert len(dns_rule["to"]) == 1
        to_entry = dns_rule["to"][0]

        # Both selectors must be present and empty to match "any pod in any namespace"
        assert "namespaceSelector" in to_entry, (
            "DNS rule must have namespaceSelector for cross-namespace access"
        )
        assert "podSelector" in to_entry, (
            "DNS rule must have podSelector alongside namespaceSelector"
        )
        assert to_entry["namespaceSelector"] == {}, "namespaceSelector should be empty"
        assert to_entry["podSelector"] == {}, "podSelector should be empty"


class TestCreateSessionWithProxy:
    """Tests for create_session with proxy deployment."""

    @patch("subprocess.run")
    def test_creates_proxy_when_allowed_domains_set(self, mock_run: MagicMock) -> None:
        """create_session creates proxy when allowed_domains is set."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude-base-centos10:v1",
            allowed_domains=[".googleapis.com", ".google.com"],
        )

        backend.create_session(config)

        # Verify proxy deployment was created
        calls_str = str(mock_run.call_args_list)
        assert "paude-proxy-test-session" in calls_str

        # Check that proxy image was derived correctly
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c for c in apply_calls if "Deployment" in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

    @patch("subprocess.run")
    def test_proxy_gets_allowed_domains_env_var(self, mock_run: MagicMock) -> None:
        """create_session passes ALLOWED_DOMAINS to proxy deployment."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude-base-centos10:v1",
            allowed_domains=[".googleapis.com", ".example.com"],
        )

        backend.create_session(config)

        # Find the proxy Deployment creation
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c
            for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

        # Check ALLOWED_DOMAINS env var
        deployment_spec = json.loads(deployment_calls[0][1]["input"])
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]
        env_dict = {e["name"]: e["value"] for e in container.get("env", [])}
        assert "ALLOWED_DOMAINS" in env_dict
        assert ".googleapis.com" in env_dict["ALLOWED_DOMAINS"]
        assert ".example.com" in env_dict["ALLOWED_DOMAINS"]

    @patch("subprocess.run")
    def test_sets_proxy_env_vars_when_allowed_domains_set(
        self, mock_run: MagicMock
    ) -> None:
        """create_session sets HTTP_PROXY env vars when allowed_domains is set."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude:v1",
            allowed_domains=[".googleapis.com"],
        )

        backend.create_session(config)

        # Find StatefulSet creation
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        sts_calls = [
            c for c in apply_calls if "StatefulSet" in str(c[1].get("input", ""))
        ]
        assert len(sts_calls) >= 1

        sts_spec = json.loads(sts_calls[0][1]["input"])
        container = sts_spec["spec"]["template"]["spec"]["containers"][0]
        env_dict = {e["name"]: e["value"] for e in container["env"]}

        expected_proxy = "http://paude-proxy-test-session:3128"
        assert env_dict.get("HTTP_PROXY") == expected_proxy
        assert env_dict.get("HTTPS_PROXY") == expected_proxy
        assert env_dict.get("http_proxy") == expected_proxy
        assert env_dict.get("https_proxy") == expected_proxy
        assert env_dict.get("NO_PROXY") == "localhost,127.0.0.1"
        assert env_dict.get("no_proxy") == "localhost,127.0.0.1"

    @patch("subprocess.run")
    def test_no_proxy_when_allowed_domains_none(self, mock_run: MagicMock) -> None:
        """create_session does not create proxy when allowed_domains=None."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude:v1",
            allowed_domains=None,
        )

        backend.create_session(config)

        # Verify no proxy deployment was created
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c
            for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) == 0

        # Verify no proxy env vars in StatefulSet
        sts_calls = [
            c for c in apply_calls if "StatefulSet" in str(c[1].get("input", ""))
        ]
        if sts_calls:
            sts_spec = json.loads(sts_calls[0][1]["input"])
            container = sts_spec["spec"]["template"]["spec"]["containers"][0]
            env_dict = {e["name"]: e["value"] for e in container["env"]}
            assert "HTTP_PROXY" not in env_dict
            assert "HTTPS_PROXY" not in env_dict


class TestDeleteSessionWithProxy:
    """Tests for delete_session cleaning up proxy resources."""

    @patch("subprocess.run")
    def test_deletes_proxy_resources(self, mock_run: MagicMock) -> None:
        """delete_session deletes proxy Deployment and Service."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "apiVersion": "apps/v1",
                            "kind": "StatefulSet",
                            "metadata": {"name": "paude-test"},
                        }
                    ),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.delete_session("test", confirm=True)

        # Verify proxy resources were deleted
        calls_str = str(mock_run.call_args_list)
        assert "paude-proxy-test" in calls_str
        assert "deployment" in calls_str.lower()
        assert "service" in calls_str.lower()


class TestDeleteProxyResources:
    """Tests for _proxy.delete_resources method."""

    @patch("subprocess.run")
    def test_deletes_deployment_and_service(self, mock_run: MagicMock) -> None:
        """_proxy.delete_resources deletes both Deployment and Service."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.delete_resources("my-session")

        calls = mock_run.call_args_list
        delete_calls = [c for c in calls if "delete" in str(c)]

        # Should have 2 delete calls (Deployment and Service)
        assert len(delete_calls) >= 2

        # Check Deployment deletion
        deployment_deleted = any(
            "deployment" in str(c) and "paude-proxy-my-session" in str(c)
            for c in delete_calls
        )
        assert deployment_deleted

        # Check Service deletion
        assert any(
            "service" in str(c) and "paude-proxy-my-session" in str(c)
            for c in delete_calls
        )


class TestEnsureProxyNetworkPolicy:
    """Tests for _proxy.ensure_proxy_network_policy method."""

    @patch("subprocess.run")
    def test_creates_permissive_egress_policy(self, mock_run: MagicMock) -> None:
        """_proxy.ensure_proxy_network_policy creates policy allowing all egress."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._proxy.ensure_proxy_network_policy("my-session")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1

        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        assert spec["kind"] == "NetworkPolicy"
        assert spec["metadata"]["name"] == "paude-proxy-egress-my-session"
        assert spec["metadata"]["labels"]["app"] == "paude-proxy"

        # Verify pod selector targets proxy
        selector = spec["spec"]["podSelector"]["matchLabels"]
        assert selector["app"] == "paude-proxy"
        assert selector["paude.io/session-name"] == "my-session"

        # Verify egress allows all (empty rule)
        egress = spec["spec"]["egress"]
        assert len(egress) == 1
        assert egress[0] == {}  # Empty rule = allow all


class TestProxyImageDerivation:
    """Tests for proxy image derivation logic."""

    @patch("subprocess.run")
    def test_derives_proxy_image_from_main_image(self, mock_run: MagicMock) -> None:
        """Proxy image is derived by replacing image name pattern."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test",
            workspace=Path("/project"),
            image="quay.io/bbrowning/paude-base-centos10:v1.2.3",
            allowed_domains=[".googleapis.com"],
        )

        backend.create_session(config)

        # Find the Deployment apply call
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c
            for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

        deployment_spec = json.loads(deployment_calls[0][1]["input"])
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]

        # Verify the proxy image was derived correctly
        assert container["image"] == "quay.io/bbrowning/paude-proxy-centos10:v1.2.3"

    @patch("subprocess.run")
    def test_falls_back_to_default_when_pattern_not_found(
        self, mock_run: MagicMock
    ) -> None:
        """Falls back to default proxy image when pattern doesn't match."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test",
            workspace=Path("/project"),
            image="custom-registry.io/some-other-image:latest",  # No pattern match
            allowed_domains=[".googleapis.com"],
        )

        backend.create_session(config)

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c
            for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

        deployment_spec = json.loads(deployment_calls[0][1]["input"])
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]

        # Verify fallback to versioned proxy image from registry
        from paude import __version__

        assert (
            container["image"]
            == f"quay.io/bbrowning/paude-proxy-centos10:{__version__}"
        )


class TestStartSessionWaitsForProxy:
    """Tests for start_session waiting for proxy."""

    @patch("subprocess.run")
    def test_waits_for_proxy_when_exists(self, mock_run: MagicMock) -> None:
        """start_session waits for proxy deployment when it exists."""
        call_order = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            if "get" in cmd and "statefulset" in cmd:
                call_order.append("get_statefulset")
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "metadata": {
                                "name": "paude-test",
                                "annotations": {"paude.io/workspace": ""},
                            },
                            "spec": {"replicas": 0},
                        }
                    ),
                    stderr="",
                )
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                if "jsonpath" not in cmd_str:
                    call_order.append("get_proxy_deployment")
                    return MagicMock(returncode=0, stdout="{}", stderr="")
            if "jsonpath" in cmd_str and "readyReplicas" in cmd_str:
                call_order.append("check_proxy_ready")
                return MagicMock(returncode=0, stdout="1", stderr="")
            if "scale" in cmd:
                call_order.append("scale")
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="Running", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._pod_waiter, "wait_for_ready"):
            with patch.object(backend, "connect_session", return_value=0):
                backend.start_session("test")

        # Verify proxy check happened
        assert "get_proxy_deployment" in call_order
        assert "check_proxy_ready" in call_order

    @patch("subprocess.run")
    def test_skips_proxy_wait_when_not_exists(self, mock_run: MagicMock) -> None:
        """start_session skips proxy wait when no proxy deployment."""
        call_order = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "metadata": {
                                "name": "paude-test",
                                "annotations": {"paude.io/workspace": ""},
                            },
                            "spec": {"replicas": 0},
                        }
                    ),
                    stderr="",
                )
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                call_order.append("get_proxy_deployment")
                # Proxy doesn't exist
                return MagicMock(returncode=1, stdout="", stderr="not found")
            if "readyReplicas" in cmd_str:
                call_order.append("check_proxy_ready")
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="Running", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._pod_waiter, "wait_for_ready"):
            with patch.object(backend, "connect_session", return_value=0):
                backend.start_session("test")

        # Verify proxy was checked but not waited for
        assert "get_proxy_deployment" in call_order
        assert "check_proxy_ready" not in call_order


class TestConnectSessionRefreshesCredentials:
    """Tests for connect_session syncing credentials/config."""

    @patch("subprocess.run")
    def test_connect_session_full_sync_on_first_connect(
        self, mock_run: MagicMock
    ) -> None:
        """connect_session calls _syncer.sync_full_config on first connect.

        When .ready doesn't exist (first connect after start), full config
        sync is performed including gcloud, claude config, and gitconfig.
        """

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])

            if "get" in cmd and "pod" in cmd:
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Mock _syncer.is_config_synced to return False (first connect)
        with patch.object(backend._syncer, "is_config_synced", return_value=False):
            with patch.object(backend._syncer, "sync_full_config") as mock_full_sync:
                with patch.object(backend._syncer, "sync_credentials") as mock_creds:
                    with patch("subprocess.run", mock_run):
                        backend.connect_session("test")

                # First connect: full config sync
                mock_full_sync.assert_called_once()
                mock_creds.assert_not_called()
                assert "paude-test-0" in str(mock_full_sync.call_args)

    @patch("subprocess.run")
    def test_connect_session_credentials_only_on_reconnect(
        self, mock_run: MagicMock
    ) -> None:
        """connect_session calls _syncer.sync_credentials on reconnect.

        When .ready exists (reconnect), only gcloud credentials are refreshed
        for faster reconnection.
        """

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])

            if "get" in cmd and "pod" in cmd:
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Mock _syncer.is_config_synced to return True (reconnect)
        with patch.object(backend._syncer, "is_config_synced", return_value=True):
            with patch.object(backend._syncer, "sync_full_config") as mock_full_sync:
                with patch.object(backend._syncer, "sync_credentials") as mock_creds:
                    with patch("subprocess.run", mock_run):
                        backend.connect_session("test")

                # Reconnect: credentials only
                mock_creds.assert_called_once()
                mock_full_sync.assert_not_called()
                assert "paude-test-0" in str(mock_creds.call_args)

    @patch("subprocess.run")
    def test_connect_session_does_not_sync_when_pod_not_running(
        self, mock_run: MagicMock
    ) -> None:
        """connect_session does not sync if pod is not running."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Pending", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._syncer, "sync_full_config") as mock_full_sync:
            with patch.object(backend._syncer, "sync_credentials") as mock_creds:
                result = backend.connect_session("test")

                # Should return 1 (error) without syncing
                assert result == 1
                mock_full_sync.assert_not_called()
                mock_creds.assert_not_called()

    @patch("subprocess.run")
    def test_connect_session_does_not_sync_when_pod_not_found(
        self, mock_run: MagicMock
    ) -> None:
        """connect_session does not sync if pod doesn't exist."""
        # Simulate pod not found - returncode != 0
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="pod not found"
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._syncer, "sync_full_config") as mock_full_sync:
            with patch.object(backend._syncer, "sync_credentials") as mock_creds:
                result = backend.connect_session("test")

                # Should return 1 (error) without syncing
                assert result == 1
                mock_full_sync.assert_not_called()
                mock_creds.assert_not_called()

    @patch("subprocess.run")
    def test_connect_session_shows_empty_workspace_message(
        self, mock_run: MagicMock, capsys: Any
    ) -> None:
        """connect_session shows message when workspace is empty."""
        call_order = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            if "get" in cmd and "pod" in cmd:
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Empty workspace - no .git directory
            if "test" in cmd and "-d" in cmd and ".git" in cmd_str:
                call_order.append("check_git_dir")
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._syncer, "is_config_synced", return_value=True):
            with patch.object(backend._syncer, "sync_credentials"):
                backend.connect_session("test")

        captured = capsys.readouterr()
        assert "Workspace is empty" in captured.err
        assert "paude remote add test" in captured.err
        assert "git push paude-test main" in captured.err


class TestIsConfigSynced:
    """Tests for _syncer.is_config_synced method."""

    @patch("subprocess.run")
    def test_returns_true_when_ready_exists(self, mock_run: MagicMock) -> None:
        """_syncer.is_config_synced returns True when .ready file exists."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._syncer.is_config_synced("paude-test-0")

        assert result is True
        # Verify it runs test -f /credentials/.ready
        cmd_str = str(mock_run.call_args)
        assert "test" in cmd_str
        assert "-f" in cmd_str
        assert "/credentials/.ready" in cmd_str

    @patch("subprocess.run")
    def test_returns_false_when_ready_missing(self, mock_run: MagicMock) -> None:
        """_syncer.is_config_synced returns False when .ready file doesn't exist."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._syncer.is_config_synced("paude-test-0")

        assert result is False


class TestSyncCredentialsToPod:
    """Tests for _syncer.sync_credentials method (fast credential refresh)."""

    @patch("subprocess.run")
    def test_syncs_gcloud_files(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_credentials syncs gcloud credential files."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake gcloud credentials
        gcloud_dir = tmp_path / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}")
        (gcloud_dir / "credentials.db").write_text("")
        (gcloud_dir / "access_tokens.db").write_text("")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_credentials("paude-test-0", verbose=True)

        # Verify oc cp was called for gcloud files
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        gcloud_cp_calls = [c for c in cp_calls if "gcloud" in str(c)]
        assert len(gcloud_cp_calls) >= 1

    @patch("subprocess.run")
    def test_touches_ready_marker(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_credentials touches .ready marker."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_credentials("paude-test-0")

        # Verify touch .ready was called
        touch_calls = [
            c
            for c in mock_run.call_args_list
            if "touch" in str(c) and ".ready" in str(c)
        ]
        assert len(touch_calls) >= 1

    @patch("subprocess.run")
    def test_does_not_sync_claude_or_git_config(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_credentials only syncs gcloud, not claude/git config."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake configs
        gcloud_dir = tmp_path / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}")

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        (tmp_path / ".gitconfig").write_text("[user]\nname = Test")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_credentials("paude-test-0")

        # Verify no claude or git syncing
        all_calls_str = str(mock_run.call_args_list)
        assert "rsync" not in all_calls_str  # No rsync for claude dir
        assert "gitconfig" not in all_calls_str  # No gitconfig sync


class TestSyncCursorAuthJson:
    """Tests for Cursor auth.json sync in ConfigSyncer."""

    @patch("subprocess.run")
    def test_sync_config_files_syncs_auth_json_for_cursor(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_files syncs auth.json for cursor agent."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake cursor config and auth.json
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli-config.json").write_text("{}")
        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text('{"accessToken": "test"}')

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer._sync_config_files("cursor")

        # Verify oc cp was called for cursor-auth.json
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        auth_cp_calls = [c for c in cp_calls if "cursor-auth.json" in str(c)]
        assert len(auth_cp_calls) >= 1

    @patch("subprocess.run")
    def test_sync_config_files_does_not_sync_auth_json_for_claude(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_files does NOT sync auth.json for non-cursor agents."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake claude config
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        # Create fake cursor auth.json (should NOT be synced for claude agent)
        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text('{"accessToken": "test"}')

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer._sync_config_files("claude")

        # Verify cursor-auth.json was NOT synced
        all_calls_str = str(mock_run.call_args_list)
        assert "cursor-auth.json" not in all_calls_str

    @patch("subprocess.run")
    def test_sync_credentials_syncs_auth_json_for_cursor(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_credentials syncs auth.json on reconnect for cursor agent."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text('{"accessToken": "test"}')

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_credentials("test-pod-0", agent_name="cursor")

        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        auth_cp_calls = [c for c in cp_calls if "cursor-auth.json" in str(c)]
        assert len(auth_cp_calls) >= 1

    @patch("subprocess.run")
    def test_sync_credentials_does_not_sync_auth_json_for_claude(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_credentials does NOT sync auth.json for non-cursor agents."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text('{"accessToken": "test"}')

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_credentials("test-pod-0", agent_name="claude")

        all_calls_str = str(mock_run.call_args_list)
        assert "cursor-auth.json" not in all_calls_str


class TestCreateSessionWithProxyNetworkPolicy:
    """Tests for create_session creating proxy NetworkPolicy."""

    @patch("subprocess.run")
    def test_creates_proxy_network_policy(self, mock_run: MagicMock) -> None:
        """create_session creates NetworkPolicy for proxy."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return "1" for deployment readiness check (proxy)
            if "get" in cmd and "deployment" in cmd and "readyReplicas" in str(cmd):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/project"),
            image="quay.io/test/paude:v1",
            allowed_domains=[".googleapis.com"],
        )

        backend.create_session(config)

        # Find proxy NetworkPolicy
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        proxy_policy_calls = [
            c
            for c in apply_calls
            if "paude-proxy-egress-test-session" in str(c[1].get("input", ""))
        ]
        assert len(proxy_policy_calls) >= 1


class TestSyncConfigToPod:
    """Tests for _syncer.sync_full_config method (tmpfs-based credential sync)."""

    @patch("subprocess.run")
    def test_creates_config_directory_structure(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config creates /credentials directory structure."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find the exec call that creates the directory structure
        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in str(c) and "mkdir -p" in str(c)
        ]
        assert len(exec_calls) >= 1

        # Verify the command includes mkdir (idempotent) and chmod, but NOT rm -rf
        # Using mkdir -p instead of rm -rf preserves working directories
        exec_cmd = str(exec_calls[0])
        assert "rm -rf /credentials" not in exec_cmd
        assert "mkdir -p /credentials/gcloud /credentials/claude" in exec_cmd
        assert "chmod -R g+rwX /credentials" in exec_cmd

    @patch("subprocess.run")
    def test_syncs_gcloud_credentials(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config syncs gcloud credential files."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock gcloud files
        gcloud_dir = tmp_path / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}")
        (gcloud_dir / "credentials.db").write_text("db")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find oc cp calls for gcloud files
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)

        # Verify gcloud files are synced
        assert "application_default_credentials.json" in cp_calls_str
        assert "credentials.db" in cp_calls_str

    @patch("subprocess.run")
    def test_syncs_claude_config_files(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config syncs claude config directory via rsync."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock claude files
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("{}")
        (claude_dir / "credentials.json").write_text("{}")
        (tmp_path / ".claude.json").write_text("{}")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find rsync calls (now using rsync for ~/.claude/)
        rsync_calls = [c for c in mock_run.call_args_list if "rsync" in str(c)]
        assert len(rsync_calls) >= 1, "Should use rsync for ~/.claude/ directory"

        # Verify rsync targets claude directory
        rsync_calls_str = str(rsync_calls)
        assert ".claude" in rsync_calls_str

        # .claude.json is still synced separately via cp
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)
        assert ".claude.json" in cp_calls_str

    @patch("subprocess.run")
    def test_syncs_gitconfig(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_full_config syncs gitconfig."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock gitconfig
        (tmp_path / ".gitconfig").write_text("[user]\nname = Test")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find oc cp call for gitconfig
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)

        assert ".gitconfig" in cp_calls_str
        assert "/credentials/gitconfig" in cp_calls_str

    @patch("subprocess.run")
    def test_syncs_global_gitignore(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_full_config syncs global gitignore from ~/.config/git/ignore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock global gitignore
        git_config_dir = tmp_path / ".config" / "git"
        git_config_dir.mkdir(parents=True)
        (git_config_dir / "ignore").write_text("**/.claude/settings.local.json\n")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find oc cp call for global gitignore
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)

        assert ".config/git/ignore" in cp_calls_str
        assert "/credentials/gitignore-global" in cp_calls_str

    @patch("subprocess.run")
    def test_skips_global_gitignore_when_missing(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config skips global gitignore when it doesn't exist."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Don't create the global gitignore file

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Should not have a cp call for gitignore-global
        cp_calls_str = str(mock_run.call_args_list)
        assert "gitignore-global" not in cp_calls_str

    @patch("subprocess.run")
    def test_sync_full_config_copies_global_gitignore(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config copies global gitignore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock global gitignore
        git_config_dir = tmp_path / ".config" / "git"
        git_config_dir.mkdir(parents=True)
        (git_config_dir / "ignore").write_text("*.log\n")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Verify oc cp was called for gitignore-global
        cp_calls = [c for c in mock_run.call_args_list if "gitignore-global" in str(c)]
        assert len(cp_calls) >= 1

    @patch("subprocess.run")
    def test_creates_ready_marker(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_full_config creates .ready marker file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find the exec call that creates the .ready marker
        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in str(c) and ".ready" in str(c)
        ]
        # Should have at least 2 calls: one to create .ready, one to verify
        assert len(exec_calls) >= 2

        # Verify touch .ready is in the create command
        create_cmd = str(exec_calls[0])
        assert "touch /credentials/.ready" in create_cmd

        # Verify chmod is wrapped with error suppression (non-fatal)
        assert "2>/dev/null || true" in create_cmd

        # Verify there's a test -f call to verify .ready was created
        verify_cmd = str(exec_calls[1])
        assert "test" in verify_cmd
        assert "/credentials/.ready" in verify_cmd

    @patch("subprocess.run")
    def test_warns_when_ready_marker_fails(
        self, mock_run: MagicMock, tmp_path: Path, capsys: Any
    ) -> None:
        """_syncer.sync_full_config warns if .ready marker creation fails."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            # Fail the test -f verification
            if "test" in cmd and "-f" in cmd and ".ready" in cmd_str:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        captured = capsys.readouterr()
        assert "Warning: Failed to create" in captured.err
        assert ".ready" in captured.err

    @patch("subprocess.run")
    def test_handles_missing_files_gracefully(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config doesn't fail when files are missing."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # No credential files exist in tmp_path

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Should not raise
        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Should still create the directory structure and .ready marker
        calls_str = str(mock_run.call_args_list)
        assert "mkdir -p /credentials/gcloud /credentials/claude" in calls_str
        assert ".ready" in calls_str

    @patch("subprocess.run")
    def test_raises_on_mkdir_failure(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """_syncer.sync_full_config raises OpenShiftError when mkdir fails."""
        from paude.backends.openshift import OpenShiftError

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            # Fail the mkdir command (first exec call)
            if "exec" in cmd and "mkdir" in str(cmd):
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="mkdir: cannot create directory: No space left",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            with pytest.raises(OpenShiftError) as exc_info:
                backend._syncer.sync_full_config("test-pod-0")

        assert "Failed to prepare config directory" in str(exc_info.value)

    @patch("subprocess.run")
    def test_exec_calls_use_extended_timeout(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config exec calls use OC_EXEC_TIMEOUT (not default)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find all exec calls (mkdir/chmod operations)
        exec_calls = [
            c for c in mock_run.call_args_list if len(c[0]) > 0 and "exec" in c[0][0]
        ]

        # There should be at least 2 exec calls:
        # 1. mkdir + chmod for config directory prep
        # 2. chmod + touch for .ready marker
        assert len(exec_calls) >= 2, (
            f"Expected at least 2 exec calls, got {len(exec_calls)}"
        )

        # All exec calls should use the extended timeout
        for call in exec_calls:
            timeout = call[1].get("timeout")
            assert timeout == OpenShiftBackend.OC_EXEC_TIMEOUT, (
                f"exec call should use OC_EXEC_TIMEOUT ({OpenShiftBackend.OC_EXEC_TIMEOUT}), "
                f"got {timeout}. Call: {call}"
            )


class TestSyncConfigWithPlugins:
    """Tests for _sync_config_to_pod with full ~/.claude/ sync including plugins."""

    @patch("subprocess.run")
    def test_sync_config_uses_rsync_with_excludes(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config uses rsync with config excludes for ~/.claude/."""
        # Create mock claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        # Find rsync calls
        rsync_calls = [
            c
            for c in mock_run.call_args_list
            if c[0] and len(c[0]) > 0 and "rsync" in c[0][0]
        ]

        # Should have at least one rsync call for claude directory
        assert len(rsync_calls) >= 1

        # Verify excludes are passed
        rsync_cmd = rsync_calls[0][0][0]
        assert "--exclude" in rsync_cmd

    @patch("subprocess.run")
    def test_sync_config_calls_rewrite_plugin_paths(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config calls _rewrite_plugin_paths after rsync."""
        # Create mock claude directory with plugins subdirectory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")
        (claude_dir / "plugins").mkdir()

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch.object(backend._syncer, "_rewrite_plugin_paths") as mock_rewrite:
                backend._syncer.sync_full_config("test-pod-0")
                mock_rewrite.assert_called_once()

    @patch("subprocess.run")
    def test_sync_config_skips_rewrite_when_no_plugins_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config skips _rewrite_plugin_paths when no plugins dir."""
        # Create mock claude directory WITHOUT plugins subdirectory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch.object(backend._syncer, "_rewrite_plugin_paths") as mock_rewrite:
                backend._syncer.sync_full_config("test-pod-0")
                mock_rewrite.assert_not_called()

    @patch("subprocess.run")
    def test_sync_config_handles_missing_claude_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config handles missing ~/.claude/ gracefully."""
        # Don't create claude directory

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            # Should not raise
            backend._syncer.sync_full_config("test-pod-0")

        # Should not have rsync calls for claude directory
        rsync_calls = [
            c
            for c in mock_run.call_args_list
            if c[0] and len(c[0]) > 0 and "rsync" in c[0][0] and ".claude" in str(c)
        ]
        assert len(rsync_calls) == 0

    @patch("subprocess.run")
    def test_sync_config_skips_rewrite_on_rsync_failure(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_syncer.sync_full_config does NOT call _rewrite_plugin_paths when rsync fails."""
        # Create mock claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            # Fail rsync calls
            if "rsync" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="error: rsync failed",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch.object(backend._syncer, "_rewrite_plugin_paths") as mock_rewrite:
                backend._syncer.sync_full_config("test-pod-0")
                # Should NOT be called because rsync failed
                mock_rewrite.assert_not_called()

    @patch("subprocess.run")
    def test_sync_config_prints_warning_on_rsync_failure(
        self, mock_run: MagicMock, tmp_path: Path, capsys: Any
    ) -> None:
        """_syncer.sync_full_config prints warning when rsync fails."""
        # Create mock claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            if "rsync" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="error: rsync failed",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._syncer.sync_full_config("test-pod-0")

        captured = capsys.readouterr()
        assert "Failed to copy agent config directory" in captured.err
        assert "plugins may not work" in captured.err


class TestRewritePluginPaths:
    """Tests for _rewrite_plugin_paths method."""

    @staticmethod
    def _make_claude_agent() -> Any:
        """Create a claude agent for testing _rewrite_plugin_paths."""
        from paude.agents import get_agent

        return get_agent("claude")

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_uses_jq(self, mock_run: MagicMock) -> None:
        """_rewrite_plugin_paths uses jq to rewrite installed_plugins.json."""

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect
        agent = self._make_claude_agent()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"
        backend._syncer._rewrite_plugin_paths("/credentials/claude", agent, Path.home())

        # Find exec calls with jq
        jq_calls = [
            c
            for c in mock_run.call_args_list
            if c[0] and len(c[0]) > 0 and "exec" in c[0][0] and "jq" in str(c)
        ]

        # Should have two jq calls (installed_plugins.json and known_marketplaces.json)
        assert len(jq_calls) >= 2

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_targets_correct_files(
        self, mock_run: MagicMock
    ) -> None:
        """_rewrite_plugin_paths rewrites both plugin metadata files."""

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect
        agent = self._make_claude_agent()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"
        backend._syncer._rewrite_plugin_paths("/credentials/claude", agent, Path.home())

        # Check for installed_plugins.json rewrite
        installed_plugins_calls = [
            c for c in mock_run.call_args_list if "installed_plugins.json" in str(c)
        ]
        assert len(installed_plugins_calls) >= 1

        # Check for known_marketplaces.json rewrite
        known_marketplaces_calls = [
            c for c in mock_run.call_args_list if "known_marketplaces.json" in str(c)
        ]
        assert len(known_marketplaces_calls) >= 1

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_uses_correct_container_path(
        self, mock_run: MagicMock
    ) -> None:
        """_rewrite_plugin_paths rewrites to /home/paude/.claude/plugins/."""

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect
        agent = self._make_claude_agent()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"
        backend._syncer._rewrite_plugin_paths("/credentials/claude", agent, Path.home())

        # Check that the container path is used
        all_calls_str = str(mock_run.call_args_list)
        assert "/home/paude/.claude/plugins" in all_calls_str

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_handles_null_installpath(
        self, mock_run: MagicMock
    ) -> None:
        """_rewrite_plugin_paths jq expression handles null/missing installPath."""

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect
        agent = self._make_claude_agent()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"
        backend._syncer._rewrite_plugin_paths("/credentials/claude", agent, Path.home())

        # The jq expression should include null-safety check
        all_calls_str = str(mock_run.call_args_list)
        # Check for the conditional that guards against null installPath
        assert "if .installPath then" in all_calls_str

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_handles_null_installlocation(
        self, mock_run: MagicMock
    ) -> None:
        """_rewrite_plugin_paths jq handles null/missing installLocation."""

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect
        agent = self._make_claude_agent()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._syncer._target = "test-pod-0"
        backend._syncer._rewrite_plugin_paths("/credentials/claude", agent, Path.home())

        # The jq expression for known_marketplaces should include null-safety
        all_calls_str = str(mock_run.call_args_list)
        assert "if .value.installLocation then" in all_calls_str


class TestClaudeConfigExcludes:
    """Tests for ClaudeAgent config_excludes (canonical source of truth)."""

    def test_config_excludes_contains_expected_patterns(self) -> None:
        """config_excludes contains session-specific and cache patterns."""
        from paude.agents.claude import ClaudeAgent

        excludes = ClaudeAgent().config.config_excludes

        # Session-specific patterns (anchored with leading /)
        assert "/history.jsonl" in excludes
        assert "/tasks" in excludes
        assert "/todos" in excludes
        assert "/session-env" in excludes

        # Cache patterns (anchored to only match top-level cache)
        assert "/cache" in excludes
        assert "/stats-cache.json" in excludes

        # Git metadata
        assert "/.git" in excludes

    def test_config_excludes_uses_anchored_patterns(self) -> None:
        """config_excludes uses anchored patterns to not exclude plugins/cache."""
        from paude.agents.claude import ClaudeAgent

        excludes = ClaudeAgent().config.config_excludes

        # All patterns should be anchored (start with /) to prevent
        # accidentally excluding nested directories like plugins/cache
        for pattern in excludes:
            assert pattern.startswith("/"), (
                f"Pattern '{pattern}' should be anchored with leading / "
                "to prevent excluding nested directories"
            )

    def test_config_excludes_does_not_contain_plugins(self) -> None:
        """config_excludes does not exclude plugins directory."""
        from paude.agents.claude import ClaudeAgent

        excludes = ClaudeAgent().config.config_excludes

        assert "plugins" not in excludes


class TestEnsureProxyImageViaBuild:
    """Tests for ensure_proxy_image_via_build method."""

    @patch("subprocess.run")
    def test_ensure_proxy_image_via_build_creates_build_context(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """ensure_proxy_image_via_build creates build context from proxy dir."""
        # Create mock proxy directory
        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:9")
        (proxy_dir / "squid.conf").write_text("http_port 3128")
        (proxy_dir / "entrypoint.sh").write_text("#!/bin/bash")

        def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            # Simulate image does not exist
            if "get" in cmd and "imagestreamtag" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # Simulate buildconfig does not exist
            if "get" in cmd and "buildconfig" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # Simulate successful build
            if "start-build" in cmd:
                return MagicMock(
                    returncode=0, stdout="build/paude-proxy-abc123-1 started", stderr=""
                )
            # Simulate build completion
            if "get" in cmd and "build" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Complete", stderr="")
            # Simulate imagestream reference
            if "get" in cmd and "imagestream" in cmd and "jsonpath" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout="image-registry.svc:5000/test-ns/paude-proxy-abc123",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Mock build log streaming
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            mock_popen.return_value.terminate = MagicMock()
            result = backend.ensure_proxy_image_via_build(
                script_dir=tmp_path,
                force_rebuild=False,
                session_name="test-session",
            )

        assert "paude-proxy" in result
        # Verify start-build was called
        start_build_calls = [
            c for c in mock_run.call_args_list if "start-build" in str(c)
        ]
        assert len(start_build_calls) >= 1

    @patch("subprocess.run")
    def test_ensure_proxy_image_via_build_reuses_existing(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """ensure_proxy_image_via_build reuses existing image when available."""
        # Create mock proxy directory
        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:9")
        (proxy_dir / "squid.conf").write_text("http_port 3128")
        (proxy_dir / "entrypoint.sh").write_text("#!/bin/bash")

        def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            # Simulate image exists
            if "get" in cmd and "imagestreamtag" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            # Simulate imagestream reference
            if "get" in cmd and "imagestream" in cmd and "jsonpath" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout="image-registry.svc:5000/test-ns/paude-proxy-abc123",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend.ensure_proxy_image_via_build(
            script_dir=tmp_path,
            force_rebuild=False,
        )

        assert "paude-proxy" in result
        # Verify no start-build was called
        start_build_calls = [
            c for c in mock_run.call_args_list if "start-build" in str(c)
        ]
        assert len(start_build_calls) == 0

    @patch("subprocess.run")
    def test_ensure_proxy_image_via_build_force_rebuild(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """ensure_proxy_image_via_build rebuilds when force_rebuild=True."""
        # Create mock proxy directory
        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:9")
        (proxy_dir / "squid.conf").write_text("http_port 3128")
        (proxy_dir / "entrypoint.sh").write_text("#!/bin/bash")

        def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            # Simulate successful build
            if "start-build" in cmd:
                return MagicMock(
                    returncode=0, stdout="build/paude-proxy-abc123-1 started", stderr=""
                )
            # Simulate build completion
            if "get" in cmd and "build" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Complete", stderr="")
            # Simulate imagestream reference
            if "get" in cmd and "imagestream" in cmd and "jsonpath" in str(cmd):
                return MagicMock(
                    returncode=0,
                    stdout="image-registry.svc:5000/test-ns/paude-proxy-abc123",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock()
            mock_popen.return_value.terminate = MagicMock()
            result = backend.ensure_proxy_image_via_build(
                script_dir=tmp_path,
                force_rebuild=True,
            )

        assert "paude-proxy" in result
        # Verify start-build was called (forced rebuild)
        start_build_calls = [
            c for c in mock_run.call_args_list if "start-build" in str(c)
        ]
        assert len(start_build_calls) == 1

    def test_ensure_proxy_image_via_build_raises_if_no_proxy_dir(
        self, tmp_path: Path
    ) -> None:
        """ensure_proxy_image_via_build raises if proxy dir not found."""
        from paude.backends.openshift import OpenShiftError

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(OpenShiftError, match="Proxy container directory"):
            backend.ensure_proxy_image_via_build(script_dir=tmp_path)

    def test_ensure_proxy_image_via_build_raises_if_dockerfile_missing(
        self, tmp_path: Path
    ) -> None:
        """ensure_proxy_image_via_build raises if Dockerfile is missing."""
        from paude.backends.openshift import OpenShiftError

        # Create proxy directory but without Dockerfile
        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "squid.conf").write_text("http_port 3128")
        (proxy_dir / "entrypoint.sh").write_text("#!/bin/bash")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(OpenShiftError, match="Proxy Dockerfile not found"):
            backend.ensure_proxy_image_via_build(script_dir=tmp_path)


class TestSessionConfigProxyImage:
    """Tests for proxy_image field in SessionConfig."""

    def test_session_config_has_proxy_image_field(self) -> None:
        """SessionConfig has optional proxy_image field."""
        from paude.backends.base import SessionConfig

        config = SessionConfig(
            name="test",
            workspace=Path("/test"),
            image="paude:latest",
            proxy_image="paude-proxy:latest",
        )
        assert config.proxy_image == "paude-proxy:latest"

    def test_session_config_proxy_image_defaults_to_none(self) -> None:
        """SessionConfig.proxy_image defaults to None."""
        from paude.backends.base import SessionConfig

        config = SessionConfig(
            name="test",
            workspace=Path("/test"),
            image="paude:latest",
        )
        assert config.proxy_image is None

    @patch("subprocess.run")
    def test_create_session_uses_provided_proxy_image(
        self, mock_run: MagicMock
    ) -> None:
        """create_session uses config.proxy_image when provided."""
        proxy_image_used = []

        def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            input_data = kwargs.get("input")
            # Capture proxy image from deployment spec
            if "apply" in cmd and input_data and "paude-proxy" in input_data:
                proxy_image_used.append(input_data)
            # Return "Running" for pod status check
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Return ready for proxy deployment
            if (
                "get" in cmd
                and "deployment" in cmd
                and "jsonpath" in str(cmd)
                and "readyReplicas" in str(cmd)
            ):
                return MagicMock(returncode=0, stdout="1", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            allowed_domains=[".example.com"],
            proxy_image="custom-proxy:v1",
        )

        backend.create_session(config)

        # Verify custom proxy image was used
        assert len(proxy_image_used) >= 1

        # Parse deployment spec and verify exact image field
        deployment_spec = json.loads(proxy_image_used[0])
        assert deployment_spec["kind"] == "Deployment"
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "custom-proxy:v1"


class TestBuildConfigNamePrefix:
    """Tests for name_prefix parameter in build helper methods."""

    @patch("subprocess.run")
    def test_create_build_config_uses_name_prefix(self, mock_run: MagicMock) -> None:
        """_create_build_config uses name_prefix in resource names."""

        def run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else kwargs.get("args", [])
            # BuildConfig does not exist
            if "get" in cmd and "buildconfig" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # Apply succeeds
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._builder.create_build_config("abc123", name_prefix="paude-proxy")

        # Verify apply calls use the correct name
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(apply_calls) >= 1
        for call in apply_calls:
            input_data = call[1].get("input", "")
            if input_data:
                spec = json.loads(input_data)
                assert spec["metadata"]["name"] == "paude-proxy-abc123"

    @patch("subprocess.run")
    def test_start_binary_build_uses_name_prefix(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_builder.start_binary_build uses name_prefix in build config name."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-proxy-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        build_name = backend._builder.start_binary_build(
            "abc123", tmp_path, name_prefix="paude-proxy"
        )

        # Verify build name contains the prefix
        assert "paude-proxy" in build_name

        # Verify start-build was called with correct build config name
        start_calls = [c for c in mock_run.call_args_list if "start-build" in str(c)]
        assert len(start_calls) >= 1
        cmd = start_calls[0][0][0]
        assert "paude-proxy-abc123" in str(cmd)

    @patch("subprocess.run")
    def test_get_imagestream_reference_uses_name_prefix(
        self, mock_run: MagicMock
    ) -> None:
        """_builder.get_imagestream_reference uses name_prefix in imagestream name."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="image-registry.svc:5000/test-ns/paude-proxy-abc123",
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ref = backend._builder.get_imagestream_reference(
            "abc123", name_prefix="paude-proxy"
        )

        assert "paude-proxy-abc123" in ref

        # Verify get imagestream was called with correct name
        get_calls = [
            c
            for c in mock_run.call_args_list
            if "get" in str(c) and "imagestream" in str(c)
        ]
        assert len(get_calls) >= 1
        cmd = get_calls[0][0][0]
        assert "paude-proxy-abc123" in str(cmd)


class TestEnsureImageViaBuildPassesAgent:
    """Tests for ensure_image_via_build passing agent to prepare_build_context."""

    @pytest.mark.parametrize(
        ("agent_name", "expected_type"),
        [
            ("gemini", "GeminiAgent"),
            ("claude", "ClaudeAgent"),
        ],
    )
    @patch("subprocess.run")
    def test_passes_agent_to_prepare_build_context(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        agent_name: str,
        expected_type: str,
    ) -> None:
        """ensure_image_via_build passes the given agent to prepare_build_context."""
        from paude.agents import get_agent
        from paude.container.build_context import BuildContext

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend._builder, "start_binary_build") as mock_start_build:
            with patch.object(backend._builder, "wait_for_build"):
                with patch.object(
                    backend._builder, "get_imagestream_reference"
                ) as mock_get_ref:
                    with patch.object(backend._builder, "create_build_config"):
                        mock_start_build.return_value = "paude-abc123-1"
                        mock_get_ref.return_value = (
                            "image-registry.svc:5000/ns/paude-abc123:latest"
                        )

                        with patch(
                            "paude.container.image.prepare_build_context"
                        ) as mock_prep:
                            mock_prep.return_value = BuildContext(
                                context_dir=tmp_path,
                                dockerfile_path=tmp_path / "Dockerfile",
                                config_hash="abc123",
                                base_image="ubuntu:22.04",
                            )

                            backend.ensure_image_via_build(
                                config=None,
                                workspace=tmp_path,
                                agent=get_agent(agent_name),
                            )

                            mock_prep.assert_called_once()
                            agent_arg = mock_prep.call_args.kwargs.get("agent")
                            assert type(agent_arg).__name__ == expected_type
