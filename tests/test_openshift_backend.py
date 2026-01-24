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
    """Tests for _run_oc method."""

    @patch("subprocess.run")
    def test_run_oc_builds_command(self, mock_run: MagicMock) -> None:
        """_run_oc builds correct command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._run_oc("get", "pods")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args == ["oc", "get", "pods"]

    @patch("subprocess.run")
    def test_run_oc_includes_context(self, mock_run: MagicMock) -> None:
        """_run_oc includes context when specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = OpenShiftConfig(context="my-context")
        backend = OpenShiftBackend(config=config)
        backend._run_oc("get", "pods")

        args = mock_run.call_args[0][0]
        assert args == ["oc", "--context", "my-context", "get", "pods"]

    @patch("subprocess.run")
    def test_run_oc_raises_on_not_installed(self, mock_run: MagicMock) -> None:
        """_run_oc raises OcNotInstalledError when oc not found."""
        mock_run.side_effect = FileNotFoundError()

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotInstalledError):
            backend._run_oc("version")

    @patch("subprocess.run")
    def test_run_oc_raises_on_not_logged_in(self, mock_run: MagicMock) -> None:
        """_run_oc raises OcNotLoggedInError when not logged in."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: You must be logged in to the server",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotLoggedInError):
            backend._run_oc("whoami")

    @patch("subprocess.run")
    def test_run_oc_passes_input(self, mock_run: MagicMock) -> None:
        """_run_oc passes input data to subprocess."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._run_oc("apply", "-f", "-", input_data='{"kind":"Pod"}')

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["input"] == '{"kind":"Pod"}'

    def test_timeout_constants_have_expected_values(self) -> None:
        """Timeout constants are set to expected values."""
        # OC_DEFAULT_TIMEOUT: standard commands should complete quickly
        assert OpenShiftBackend.OC_DEFAULT_TIMEOUT == 30

        # OC_EXEC_TIMEOUT: exec operations may be slow after pod restart
        assert OpenShiftBackend.OC_EXEC_TIMEOUT == 120

        # RSYNC_TIMEOUT: large workspaces take time to sync
        assert OpenShiftBackend.RSYNC_TIMEOUT == 300

    @patch("subprocess.run")
    def test_run_oc_uses_default_timeout(self, mock_run: MagicMock) -> None:
        """_run_oc uses default timeout when none specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._run_oc("get", "pods")

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] == OpenShiftBackend.OC_DEFAULT_TIMEOUT

    @patch("subprocess.run")
    def test_run_oc_uses_custom_timeout(self, mock_run: MagicMock) -> None:
        """_run_oc uses custom timeout when specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._run_oc("get", "pods", timeout=60)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] == 60

    @patch("subprocess.run")
    def test_run_oc_no_timeout_when_zero(self, mock_run: MagicMock) -> None:
        """_run_oc disables timeout when 0 is specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._run_oc("get", "pods", timeout=0)

        mock_run.assert_called_once()
        assert mock_run.call_args[1]["timeout"] is None

    @patch("subprocess.run")
    def test_run_oc_raises_on_timeout(self, mock_run: MagicMock) -> None:
        """_run_oc raises OcTimeoutError when command times out."""
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["oc", "get", "pods"], timeout=30
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcTimeoutError) as exc_info:
            backend._run_oc("get", "pods")

        assert "timed out" in str(exc_info.value)
        assert "oc get pods" in str(exc_info.value)
        assert "network issues" in str(exc_info.value)


class TestCheckConnection:
    """Tests for _check_connection method."""

    @patch("subprocess.run")
    def test_returns_true_when_logged_in(self, mock_run: MagicMock) -> None:
        """_check_connection returns True when logged in."""
        mock_run.return_value = MagicMock(returncode=0, stdout="user", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._check_connection()

        assert result is True

    @patch("subprocess.run")
    def test_raises_when_not_logged_in(self, mock_run: MagicMock) -> None:
        """_check_connection raises when not logged in."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        with pytest.raises(OcNotLoggedInError):
            backend._check_connection()


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
    def test_create_session_creates_statefulset(
        self, mock_run: MagicMock
    ) -> None:
        """Create session creates a StatefulSet."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        session = backend.create_session(config)

        assert session.name == "test-session"
        assert session.status == "stopped"
        assert session.backend_type == "openshift"
        assert session.container_id == "paude-test-session-0"
        assert session.volume_name == "workspace-paude-test-session-0"

        # Verify oc apply was called for StatefulSet
        calls = mock_run.call_args_list
        apply_calls = [c for c in calls if "apply" in str(c)]
        assert len(apply_calls) > 0

    @patch("subprocess.run")
    def test_create_session_raises_if_exists(
        self, mock_run: MagicMock
    ) -> None:
        """Create session raises SessionExistsError if session exists."""
        # First call to get statefulset returns existing
        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-existing"},
                    }),
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


class TestOpenShiftDeleteSession:
    """Tests for OpenShiftBackend.delete_session."""

    @patch("subprocess.run")
    def test_delete_session_requires_confirmation(
        self, mock_run: MagicMock
    ) -> None:
        """Delete session requires confirm=True."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with pytest.raises(ValueError, match="(?i)confirm"):
            backend.delete_session("my-session", confirm=False)

    @patch("subprocess.run")
    def test_delete_session_raises_if_not_found(
        self, mock_run: MagicMock
    ) -> None:
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
    def test_delete_session_deletes_resources(
        self, mock_run: MagicMock
    ) -> None:
        """Delete session deletes StatefulSet, PVC, and credentials."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                    }),
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
    def test_start_session_raises_if_not_found(
        self, mock_run: MagicMock
    ) -> None:
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
    def test_start_session_scales_statefulset(
        self, mock_run: MagicMock
    ) -> None:
        """Start session scales StatefulSet to 1."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {
                            "name": "paude-test",
                            "annotations": {
                                "paude.io/workspace": "",
                            },
                        },
                        "spec": {"replicas": 0},
                    }),
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
        with patch.object(backend, "_wait_for_pod_ready"):
            with patch.object(backend, "connect_session", return_value=0):
                exit_code = backend.start_session("test", sync=False)

        assert exit_code == 0

        # Verify scale command was called
        calls = mock_run.call_args_list
        scale_calls = [c for c in calls if "scale" in str(c)]
        assert len(scale_calls) >= 1

        # Verify NO proxy scale command was issued (proxy doesn't exist)
        proxy_scale_calls = [
            c for c in calls
            if "scale" in str(c)
            and "deployment" in str(c)
            and "paude-proxy" in str(c)
        ]
        assert len(proxy_scale_calls) == 0

    @patch("subprocess.run")
    def test_start_session_scales_proxy_to_one(
        self, mock_run: MagicMock
    ) -> None:
        """Start session scales proxy Deployment to 1 when it exists."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {
                            "name": "paude-test",
                            "annotations": {
                                "paude.io/workspace": "",
                            },
                        },
                        "spec": {"replicas": 0},
                    }),
                    stderr="",
                )
            # Proxy deployment exists
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(returncode=0, stdout="", stderr="")
            # For scale and other commands
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(backend, "_wait_for_pod_ready"):
            with patch.object(backend, "_wait_for_proxy_ready"):
                with patch.object(backend, "connect_session", return_value=0):
                    exit_code = backend.start_session("test", sync=False)

        assert exit_code == 0

        # Verify proxy scale command was called with replicas=1
        calls = mock_run.call_args_list
        proxy_scale_calls = [
            c for c in calls
            if "scale" in str(c)
            and "deployment" in str(c)
            and "paude-proxy" in str(c)
            and "replicas=1" in str(c)
        ]
        assert len(proxy_scale_calls) == 1


class TestOpenShiftStopSession:
    """Tests for OpenShiftBackend.stop_session."""

    @patch("subprocess.run")
    def test_stop_session_scales_to_zero(
        self, mock_run: MagicMock
    ) -> None:
        """Stop session scales StatefulSet to 0."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                        "spec": {"replicas": 1},
                    }),
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
    def test_stop_session_raises_if_not_found(
        self, mock_run: MagicMock
    ) -> None:
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
    def test_stop_session_scales_proxy_to_zero(
        self, mock_run: MagicMock
    ) -> None:
        """Stop session scales proxy Deployment to 0 when it exists."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                        "spec": {"replicas": 1},
                    }),
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
            c for c in calls
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
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                        "spec": {"replicas": 1},
                    }),
                    stderr="",
                )
            # Proxy deployment doesn't exist
            if "get" in cmd and "deployment" in cmd and "paude-proxy" in cmd_str:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Error: deployments.apps \"paude-proxy-test\" not found",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Should not raise - proxy doesn't exist so we skip scaling it
        backend.stop_session("test")

        # Verify StatefulSet was still scaled to 0
        calls = mock_run.call_args_list
        sts_scale_calls = [
            c for c in calls
            if "scale" in str(c)
            and "statefulset" in str(c)
            and "replicas=0" in str(c)
        ]
        assert len(sts_scale_calls) == 1

        # Verify NO proxy scale was attempted (proxy doesn't exist)
        proxy_scale_calls = [
            c for c in calls
            if "scale" in str(c)
            and "deployment" in str(c)
            and "paude-proxy" in str(c)
        ]
        assert len(proxy_scale_calls) == 0


class TestOpenShiftListSessions:
    """Tests for OpenShiftBackend.list_sessions (new protocol)."""

    @patch("subprocess.run")
    def test_list_sessions_returns_statefulsets(
        self, mock_run: MagicMock
    ) -> None:
        """List sessions returns StatefulSets as sessions."""
        from paude.backends.openshift import _encode_path

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulsets" in cmd and "-l" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
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
                    }),
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
    def test_list_sessions_returns_empty_on_error(
        self, mock_run: MagicMock
    ) -> None:
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
    def test_get_session_returns_session_if_found(
        self, mock_run: MagicMock
    ) -> None:
        """Get session returns session if StatefulSet found."""
        from paude.backends.openshift import _encode_path

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
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
                    }),
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
    def test_get_session_returns_none_if_not_found(
        self, mock_run: MagicMock
    ) -> None:
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
        assert spec["spec"]["replicas"] == 0  # Created stopped
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


class TestCreateBuildConfig:
    """Tests for _create_build_config method."""

    @patch("subprocess.run")
    def test_creates_buildconfig_and_imagestream(
        self, mock_run: MagicMock
    ) -> None:
        """_create_build_config creates BuildConfig and ImageStream."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "buildconfig" in cmd:
                # BuildConfig doesn't exist yet
                return MagicMock(returncode=1, stdout="", stderr="not found")
            # Apply commands succeed
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._create_build_config("abc123")

        # Should have called oc apply twice (ImageStream and BuildConfig)
        calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        assert len(calls) >= 2

    @patch("subprocess.run")
    def test_skips_if_buildconfig_exists(
        self, mock_run: MagicMock
    ) -> None:
        """_create_build_config skips if BuildConfig already exists."""
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._create_build_config("abc123")

        # Should only have called get, not apply
        calls = mock_run.call_args_list
        get_calls = [c for c in calls if "get" in str(c) and "buildconfig" in str(c)]
        apply_calls = [c for c in calls if "apply" in str(c)]
        assert len(get_calls) == 1
        assert len(apply_calls) == 0


class TestStartBinaryBuild:
    """Tests for _start_binary_build method."""

    @patch("subprocess.run")
    def test_starts_build_with_from_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_start_binary_build uses --from-dir option."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        build_name = backend._start_binary_build("abc123", tmp_path)

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
        """_start_binary_build labels build when session_name provided."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._start_binary_build("abc123", tmp_path, session_name="my-session")

        # Verify label command was called (look for "label" as a command, not substring)
        calls = mock_run.call_args_list
        label_calls = [
            c for c in calls
            if len(c[0]) > 0 and "label" in c[0][0]
            and "start-build" not in str(c)
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
        """_start_binary_build does not label when session_name is None."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="build/paude-abc123-1 started", stderr=""
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._start_binary_build("abc123", tmp_path, session_name=None)

        # Verify no label command was called (look for "label" as a command, not substring)
        calls = mock_run.call_args_list
        label_calls = [
            c for c in calls
            if len(c[0]) > 0 and "label" in c[0][0]
            and "start-build" not in str(c)
        ]
        assert len(label_calls) == 0


class TestDeleteSessionBuilds:
    """Tests for _delete_session_builds method."""

    @patch("subprocess.run")
    def test_deletes_builds_with_session_label(
        self, mock_run: MagicMock
    ) -> None:
        """_delete_session_builds deletes builds with session label."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._delete_session_builds("my-session")

        # Verify delete build command was called with correct label
        calls = mock_run.call_args_list
        delete_calls = [
            c for c in calls
            if "delete" in str(c) and "build" in str(c)
        ]
        assert len(delete_calls) >= 1

        cmd = delete_calls[0][0][0]
        assert "delete" in cmd
        assert "build" in cmd
        assert any("-l" in str(arg) for arg in cmd)
        assert any("paude.io/session-name=my-session" in str(arg) for arg in cmd)


class TestDeleteSessionCallsDeleteBuilds:
    """Tests for delete_session calling _delete_session_builds."""

    @patch("subprocess.run")
    def test_delete_session_calls_delete_session_builds(
        self, mock_run: MagicMock
    ) -> None:
        """delete_session calls _delete_session_builds."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                    }),
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
    """Tests for ensure_image_via_build passing session_name to _start_binary_build."""

    @patch("subprocess.run")
    @patch("paude.backends.openshift.OpenShiftBackend._start_binary_build")
    @patch("paude.backends.openshift.OpenShiftBackend._wait_for_build")
    @patch("paude.backends.openshift.OpenShiftBackend._get_imagestream_reference")
    @patch("paude.backends.openshift.OpenShiftBackend._create_build_config")
    def test_passes_session_name_to_start_binary_build(
        self,
        mock_create_bc: MagicMock,
        mock_get_ref: MagicMock,
        mock_wait: MagicMock,
        mock_start_build: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ensure_image_via_build passes session_name to _start_binary_build."""
        # Mock imagestreamtag check to return "not found" (need to build)
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        mock_start_build.return_value = "paude-abc123-1"
        mock_get_ref.return_value = "image-registry.svc:5000/ns/paude-abc123:latest"

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.ensure_image_via_build(
            config=None,
            workspace=tmp_path,
            session_name="my-session",
        )

        # Verify _start_binary_build was called with session_name
        mock_start_build.assert_called_once()
        call_kwargs = mock_start_build.call_args
        assert call_kwargs.kwargs.get("session_name") == "my-session"

    @patch("subprocess.run")
    @patch("paude.backends.openshift.OpenShiftBackend._start_binary_build")
    @patch("paude.backends.openshift.OpenShiftBackend._wait_for_build")
    @patch("paude.backends.openshift.OpenShiftBackend._get_imagestream_reference")
    @patch("paude.backends.openshift.OpenShiftBackend._create_build_config")
    def test_passes_none_when_session_name_not_provided(
        self,
        mock_create_bc: MagicMock,
        mock_get_ref: MagicMock,
        mock_wait: MagicMock,
        mock_start_build: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ensure_image_via_build passes None when session_name not provided."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        mock_start_build.return_value = "paude-abc123-1"
        mock_get_ref.return_value = "image-registry.svc:5000/ns/paude-abc123:latest"

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.ensure_image_via_build(
            config=None,
            workspace=tmp_path,
            # session_name not provided - should default to None
        )

        # Verify _start_binary_build was called with session_name=None
        mock_start_build.assert_called_once()
        call_kwargs = mock_start_build.call_args
        assert call_kwargs.kwargs.get("session_name") is None

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
    """Tests for _get_imagestream_reference method."""

    @patch("subprocess.run")
    def test_returns_internal_reference(
        self, mock_run: MagicMock
    ) -> None:
        """_get_imagestream_reference returns internal image URL."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="image-registry.openshift-image-registry.svc:5000/test-ns/paude-abc123",
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ref = backend._get_imagestream_reference("abc123")

        assert "image-registry.openshift-image-registry.svc:5000" in ref
        assert "paude-abc123" in ref
        assert ":latest" in ref

    @patch("subprocess.run")
    def test_falls_back_to_default_registry(
        self, mock_run: MagicMock
    ) -> None:
        """_get_imagestream_reference uses default when no dockerImageRepository."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ref = backend._get_imagestream_reference("abc123")

        assert "image-registry.openshift-image-registry.svc:5000" in ref
        assert "test-ns" in ref
        assert "paude-abc123" in ref


# =============================================================================
# Proxy Pod Deployment Tests
# =============================================================================


class TestCreateProxyDeployment:
    """Tests for _create_proxy_deployment method."""

    @patch("subprocess.run")
    def test_creates_deployment_with_correct_spec(
        self, mock_run: MagicMock
    ) -> None:
        """_create_proxy_deployment creates Deployment with correct spec."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._create_proxy_deployment("my-session", "quay.io/test/proxy:latest")

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

        container = spec["spec"]["template"]["spec"]["containers"][0]
        assert container["name"] == "proxy"
        assert container["image"] == "quay.io/test/proxy:latest"
        assert container["ports"][0]["containerPort"] == 3128


class TestCreateProxyService:
    """Tests for _create_proxy_service method."""

    @patch("subprocess.run")
    def test_creates_service_with_correct_spec(
        self, mock_run: MagicMock
    ) -> None:
        """_create_proxy_service creates Service with correct spec."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        service_name = backend._create_proxy_service("my-session")

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


class TestNetworkPolicyWithProxySelector:
    """Tests for NetworkPolicy using pod selector instead of CIDRs."""

    @patch("subprocess.run")
    def test_network_policy_uses_pod_selector(
        self, mock_run: MagicMock
    ) -> None:
        """_ensure_network_policy uses pod selector for proxy access."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._ensure_network_policy("my-session")

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
    def test_network_policy_no_cidr_blocks(
        self, mock_run: MagicMock
    ) -> None:
        """_ensure_network_policy does not use CIDR blocks anymore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._ensure_network_policy("my-session")

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        call_kwargs = apply_calls[0][1]
        spec = json.loads(call_kwargs["input"])

        # Check that no ipBlock rules exist
        for rule in spec["spec"]["egress"]:
            if "to" in rule:
                for dest in rule["to"]:
                    assert "ipBlock" not in dest, "Should not use CIDR blocks"

    @patch("subprocess.run")
    def test_dns_rule_has_namespace_and_pod_selector(
        self, mock_run: MagicMock
    ) -> None:
        """DNS rule uses both namespaceSelector AND podSelector for cross-namespace access.

        OpenShift DNS pods run in openshift-dns namespace. The NetworkPolicy must
        have BOTH namespaceSelector: {} AND podSelector: {} together in the same
        'to' object to correctly match "any pod in any namespace".

        Having just namespaceSelector: {} alone doesn't work in OVN-Kubernetes.
        """
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._ensure_network_policy("my-session")

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
    def test_creates_proxy_when_network_restricted(
        self, mock_run: MagicMock
    ) -> None:
        """create_session creates proxy when network_restricted=True."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude-base-centos9:v1",
            network_restricted=True,
        )

        backend.create_session(config)

        # Verify proxy deployment was created
        calls_str = str(mock_run.call_args_list)
        assert "paude-proxy-test-session" in calls_str

        # Check that proxy image was derived correctly
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c for c in apply_calls
            if "Deployment" in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

    @patch("subprocess.run")
    def test_sets_proxy_env_vars_when_network_restricted(
        self, mock_run: MagicMock
    ) -> None:
        """create_session sets HTTP_PROXY env vars when network_restricted."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude:v1",
            network_restricted=True,
        )

        backend.create_session(config)

        # Find StatefulSet creation
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        sts_calls = [
            c for c in apply_calls
            if "StatefulSet" in str(c[1].get("input", ""))
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

    @patch("subprocess.run")
    def test_no_proxy_when_allow_network(
        self, mock_run: MagicMock
    ) -> None:
        """create_session does not create proxy when network_restricted=False."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="quay.io/test/paude:v1",
            network_restricted=False,
        )

        backend.create_session(config)

        # Verify no proxy deployment was created
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) == 0

        # Verify no proxy env vars in StatefulSet
        sts_calls = [
            c for c in apply_calls
            if "StatefulSet" in str(c[1].get("input", ""))
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
    def test_deletes_proxy_resources(
        self, mock_run: MagicMock
    ) -> None:
        """delete_session deletes proxy Deployment and Service."""

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "apiVersion": "apps/v1",
                        "kind": "StatefulSet",
                        "metadata": {"name": "paude-test"},
                    }),
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
    """Tests for _delete_proxy_resources method."""

    @patch("subprocess.run")
    def test_deletes_deployment_and_service(
        self, mock_run: MagicMock
    ) -> None:
        """_delete_proxy_resources deletes both Deployment and Service."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._delete_proxy_resources("my-session")

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
    """Tests for _ensure_proxy_network_policy method."""

    @patch("subprocess.run")
    def test_creates_permissive_egress_policy(
        self, mock_run: MagicMock
    ) -> None:
        """_ensure_proxy_network_policy creates policy allowing all egress."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._ensure_proxy_network_policy("my-session")

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
    def test_derives_proxy_image_from_main_image(
        self, mock_run: MagicMock
    ) -> None:
        """Proxy image is derived by replacing image name pattern."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test",
            workspace=Path("/project"),
            image="quay.io/bbrowning/paude-base-centos9:v1.2.3",
            network_restricted=True,
        )

        backend.create_session(config)

        # Find the Deployment apply call
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

        deployment_spec = json.loads(deployment_calls[0][1]["input"])
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]

        # Verify the proxy image was derived correctly
        assert container["image"] == "quay.io/bbrowning/paude-proxy-centos9:v1.2.3"

    @patch("subprocess.run")
    def test_falls_back_to_default_when_pattern_not_found(
        self, mock_run: MagicMock
    ) -> None:
        """Falls back to default proxy image when pattern doesn't match."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test",
            workspace=Path("/project"),
            image="custom-registry.io/some-other-image:latest",  # No pattern match
            network_restricted=True,
        )

        backend.create_session(config)

        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        deployment_calls = [
            c for c in apply_calls
            if '"kind": "Deployment"' in str(c[1].get("input", ""))
        ]
        assert len(deployment_calls) >= 1

        deployment_spec = json.loads(deployment_calls[0][1]["input"])
        container = deployment_spec["spec"]["template"]["spec"]["containers"][0]

        # Verify fallback to default proxy image
        assert container["image"] == "quay.io/bbrowning/paude-proxy-centos9:latest"


class TestStartSessionWaitsForProxy:
    """Tests for start_session waiting for proxy."""

    @patch("subprocess.run")
    def test_waits_for_proxy_when_exists(
        self, mock_run: MagicMock
    ) -> None:
        """start_session waits for proxy deployment when it exists."""
        call_order = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            if "get" in cmd and "statefulset" in cmd:
                call_order.append("get_statefulset")
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "metadata": {
                            "name": "paude-test",
                            "annotations": {"paude.io/workspace": ""},
                        },
                        "spec": {"replicas": 0},
                    }),
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

        with patch.object(backend, "_wait_for_pod_ready"):
            with patch.object(backend, "_sync_config_to_pod"):
                with patch.object(backend, "connect_session", return_value=0):
                    backend.start_session("test", sync=False)

        # Verify proxy check happened
        assert "get_proxy_deployment" in call_order
        assert "check_proxy_ready" in call_order

    @patch("subprocess.run")
    def test_skips_proxy_wait_when_not_exists(
        self, mock_run: MagicMock
    ) -> None:
        """start_session skips proxy wait when no proxy deployment."""
        call_order = []

        def run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)

            if "get" in cmd and "statefulset" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({
                        "metadata": {
                            "name": "paude-test",
                            "annotations": {"paude.io/workspace": ""},
                        },
                        "spec": {"replicas": 0},
                    }),
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

        with patch.object(backend, "_wait_for_pod_ready"):
            with patch.object(backend, "_sync_config_to_pod"):
                with patch.object(backend, "connect_session", return_value=0):
                    backend.start_session("test", sync=False)

        # Verify proxy was checked but not waited for
        assert "get_proxy_deployment" in call_order
        assert "check_proxy_ready" not in call_order


class TestCreateSessionWithProxyNetworkPolicy:
    """Tests for create_session creating proxy NetworkPolicy."""

    @patch("subprocess.run")
    def test_creates_proxy_network_policy(
        self, mock_run: MagicMock
    ) -> None:
        """create_session creates NetworkPolicy for proxy."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        from paude.backends.base import SessionConfig

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        config = SessionConfig(
            name="test-session",
            workspace=Path("/project"),
            image="quay.io/test/paude:v1",
            network_restricted=True,
        )

        backend.create_session(config)

        # Find proxy NetworkPolicy
        apply_calls = [c for c in mock_run.call_args_list if "apply" in str(c)]
        proxy_policy_calls = [
            c for c in apply_calls
            if "paude-proxy-egress-test-session" in str(c[1].get("input", ""))
        ]
        assert len(proxy_policy_calls) >= 1


class TestSyncConfigToPod:
    """Tests for _sync_config_to_pod method (PVC-based credential sync)."""

    @patch("subprocess.run")
    def test_creates_config_directory_structure(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod creates /pvc/config directory structure."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find the exec call that creates the directory structure
        exec_calls = [
            c for c in mock_run.call_args_list
            if "exec" in str(c) and "mkdir -p" in str(c)
        ]
        assert len(exec_calls) >= 1

        # Verify the command includes mkdir (idempotent) and chmod, but NOT rm -rf
        # Using mkdir -p instead of rm -rf preserves working directories
        exec_cmd = str(exec_calls[0])
        assert "rm -rf /pvc/config" not in exec_cmd
        assert "mkdir -p /pvc/config/gcloud /pvc/config/claude" in exec_cmd
        assert "chmod -R g+rwX /pvc/config" in exec_cmd

    @patch("subprocess.run")
    def test_syncs_gcloud_credentials(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod syncs gcloud credential files."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock gcloud files
        gcloud_dir = tmp_path / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text("{}")
        (gcloud_dir / "credentials.db").write_text("db")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

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
        """_sync_config_to_pod syncs claude config directory via rsync."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock claude files
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "settings.json").write_text("{}")
        (claude_dir / "credentials.json").write_text("{}")
        (tmp_path / ".claude.json").write_text("{}")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

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
    def test_syncs_gitconfig(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod syncs gitconfig."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock gitconfig
        (tmp_path / ".gitconfig").write_text("[user]\nname = Test")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find oc cp call for gitconfig
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)

        assert ".gitconfig" in cp_calls_str
        assert "/pvc/config/gitconfig" in cp_calls_str

    @patch("subprocess.run")
    def test_syncs_global_gitignore(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod syncs global gitignore from ~/.config/git/ignore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock global gitignore
        git_config_dir = tmp_path / ".config" / "git"
        git_config_dir.mkdir(parents=True)
        (git_config_dir / "ignore").write_text("**/.claude/settings.local.json\n")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find oc cp call for global gitignore
        cp_calls = [c for c in mock_run.call_args_list if "cp" in str(c)]
        cp_calls_str = str(cp_calls)

        assert ".config/git/ignore" in cp_calls_str
        assert "/pvc/config/gitignore-global" in cp_calls_str

    @patch("subprocess.run")
    def test_skips_global_gitignore_when_missing(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod skips global gitignore when it doesn't exist."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Don't create the global gitignore file

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Should not have a cp call for gitignore-global
        cp_calls_str = str(mock_run.call_args_list)
        assert "gitignore-global" not in cp_calls_str

    @patch("subprocess.run")
    def test_verbose_output_for_global_gitignore(
        self, mock_run: MagicMock, tmp_path: Path, capsys: Any
    ) -> None:
        """_sync_config_to_pod prints verbose output for global gitignore."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create mock global gitignore
        git_config_dir = tmp_path / ".config" / "git"
        git_config_dir.mkdir(parents=True)
        (git_config_dir / "ignore").write_text("*.log\n")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0", verbose=True)

        captured = capsys.readouterr()
        assert "global gitignore" in captured.err

    @patch("subprocess.run")
    def test_creates_ready_marker(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod creates .ready marker file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find the exec call that creates the .ready marker
        exec_calls = [
            c for c in mock_run.call_args_list
            if "exec" in str(c) and ".ready" in str(c)
        ]
        assert len(exec_calls) >= 1

        # Verify touch .ready is in the command
        exec_cmd = str(exec_calls[0])
        assert "touch /pvc/config/.ready" in exec_cmd

    @patch("subprocess.run")
    def test_handles_missing_files_gracefully(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod doesn't fail when files are missing."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # No credential files exist in tmp_path

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        # Should not raise
        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Should still create the directory structure and .ready marker
        calls_str = str(mock_run.call_args_list)
        assert "mkdir -p /pvc/config/gcloud /pvc/config/claude" in calls_str
        assert ".ready" in calls_str

    @patch("subprocess.run")
    def test_raises_on_mkdir_failure(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod raises OpenShiftError when mkdir fails."""
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
                backend._sync_config_to_pod("test-pod-0")

        assert "Failed to prepare config directory" in str(exc_info.value)

    @patch("subprocess.run")
    def test_exec_calls_use_extended_timeout(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod exec calls use OC_EXEC_TIMEOUT (not default)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch.object(Path, "home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find all exec calls (mkdir/chmod operations)
        exec_calls = [
            c for c in mock_run.call_args_list
            if len(c[0]) > 0 and "exec" in c[0][0]
        ]

        # There should be at least 2 exec calls:
        # 1. mkdir + chmod for config directory prep
        # 2. chmod + touch for .ready marker
        assert len(exec_calls) >= 2, f"Expected at least 2 exec calls, got {len(exec_calls)}"

        # All exec calls should use the extended timeout
        for call in exec_calls:
            timeout = call[1].get("timeout")
            assert timeout == OpenShiftBackend.OC_EXEC_TIMEOUT, (
                f"exec call should use OC_EXEC_TIMEOUT ({OpenShiftBackend.OC_EXEC_TIMEOUT}), "
                f"got {timeout}. Call: {call}"
            )


class TestRsyncWithRetry:
    """Tests for _rsync_with_retry method."""

    @patch("subprocess.run")
    def test_rsync_with_delete_flag(self, mock_run: MagicMock) -> None:
        """_rsync_with_retry includes --delete when delete=True."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._rsync_with_retry(
            "/local/path/",
            "test-pod-0:/remote/path",
            "test-ns",
            exclude_args=["--exclude", ".git"],
            delete=True,
        )

        assert result is True
        # Find the rsync call
        rsync_calls = [
            c for c in mock_run.call_args_list if "rsync" in str(c)
        ]
        assert len(rsync_calls) == 1
        rsync_cmd = str(rsync_calls[0])
        assert "--delete" in rsync_cmd

    @patch("subprocess.run")
    def test_rsync_without_delete_flag(self, mock_run: MagicMock) -> None:
        """_rsync_with_retry does not include --delete when delete=False."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._rsync_with_retry(
            "/local/path/",
            "test-pod-0:/remote/path",
            "test-ns",
            exclude_args=[],
            delete=False,
        )

        assert result is True
        rsync_calls = [
            c for c in mock_run.call_args_list if "rsync" in str(c)
        ]
        assert len(rsync_calls) == 1
        rsync_cmd = str(rsync_calls[0])
        assert "--delete" not in rsync_cmd

    @patch("subprocess.run")
    def test_rsync_default_delete_is_false(self, mock_run: MagicMock) -> None:
        """_rsync_with_retry defaults to delete=False when not specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        # Call without specifying delete parameter - should default to False
        result = backend._rsync_with_retry(
            "/local/path/",
            "test-pod-0:/remote/path",
            "test-ns",
            exclude_args=[],
        )

        assert result is True
        rsync_calls = [
            c for c in mock_run.call_args_list if "rsync" in str(c)
        ]
        assert len(rsync_calls) == 1
        rsync_cmd = str(rsync_calls[0])
        assert "--delete" not in rsync_cmd

    @patch("subprocess.run")
    def test_rsync_returns_false_on_failure(
        self, mock_run: MagicMock, capsys: Any
    ) -> None:
        """_rsync_with_retry returns False and prints error when rsync fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error: Permission denied",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._rsync_with_retry(
            "/local/path/",
            "test-pod-0:/remote/path",
            "test-ns",
            exclude_args=[],
        )

        assert result is False
        captured = capsys.readouterr()
        assert "Rsync failed" in captured.err
        assert "Permission denied" in captured.err

    @patch("subprocess.run")
    def test_rsync_prints_error_even_without_verbose(
        self, mock_run: MagicMock, capsys: Any
    ) -> None:
        """_rsync_with_retry prints error message even when verbose=False."""
        mock_run.return_value = MagicMock(
            returncode=23,
            stdout="",
            stderr="rsync error: some files could not be transferred",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend._rsync_with_retry(
            "/local/path/",
            "test-pod-0:/remote/path",
            "test-ns",
            exclude_args=[],
            verbose=False,
        )

        assert result is False
        captured = capsys.readouterr()
        assert "Rsync failed" in captured.err
        assert "some files could not be transferred" in captured.err


class TestSyncSession:
    """Tests for sync_session method."""

    @patch("subprocess.run")
    def test_sync_session_does_not_delete_directory(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_session uses mkdir -p instead of rm -rf to preserve CWD."""
        import base64

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            # Check for pod status request and return "Running"
            cmd = args[0] if args else []
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        # Mock _get_statefulset to return a session with base64-encoded workspace
        workspace_encoded = base64.b64encode(str(tmp_path).encode()).decode()
        mock_sts = {
            "metadata": {
                "annotations": {
                    "paude.io/workspace": workspace_encoded,
                },
            },
        }
        with patch.object(backend, "_get_statefulset", return_value=mock_sts):
            backend.sync_session("test-session", direction="remote")

        # Check all exec calls
        exec_calls = [
            c for c in mock_run.call_args_list
            if "exec" in str(c) and "bash" in str(c)
        ]

        # Verify no rm -rf /pvc/workspace
        for call in exec_calls:
            call_str = str(call)
            assert "rm -rf /pvc/workspace" not in call_str

        # Verify mkdir -p is used instead
        mkdir_calls = [c for c in exec_calls if "mkdir -p" in str(c)]
        assert len(mkdir_calls) >= 1

    @patch("subprocess.run")
    def test_sync_session_uses_rsync_delete(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_session uses rsync --delete for incremental cleanup."""
        import base64

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        workspace_encoded = base64.b64encode(str(tmp_path).encode()).decode()
        mock_sts = {
            "metadata": {
                "annotations": {
                    "paude.io/workspace": workspace_encoded,
                },
            },
        }
        with patch.object(backend, "_get_statefulset", return_value=mock_sts):
            backend.sync_session("test-session", direction="remote")

        # Find rsync calls
        rsync_calls = [
            c for c in mock_run.call_args_list if "rsync" in str(c)
        ]
        assert len(rsync_calls) >= 1

        # Verify --delete flag is present for remote sync
        rsync_cmd = str(rsync_calls[0])
        assert "--delete" in rsync_cmd

    @patch("subprocess.run")
    def test_sync_session_local_does_not_use_delete(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_session with direction='local' does NOT use --delete.

        Local files should not be deleted when syncing from remote.
        """
        import base64

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        workspace_encoded = base64.b64encode(str(tmp_path).encode()).decode()
        mock_sts = {
            "metadata": {
                "annotations": {
                    "paude.io/workspace": workspace_encoded,
                },
            },
        }
        with patch.object(backend, "_get_statefulset", return_value=mock_sts):
            backend.sync_session("test-session", direction="local")

        # Find rsync calls
        rsync_calls = [
            c for c in mock_run.call_args_list if "rsync" in str(c)
        ]
        assert len(rsync_calls) >= 1

        # Verify --delete flag is NOT present for local sync
        rsync_cmd = str(rsync_calls[0])
        assert "--delete" not in rsync_cmd

    @patch("subprocess.run")
    def test_sync_session_reports_failure(
        self, mock_run: MagicMock, tmp_path: Path, capsys: Any
    ) -> None:
        """sync_session prints FAILED message when rsync fails."""
        import base64

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            cmd = args[0] if args else []
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            # Fail rsync calls
            if "rsync" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="error: Permission denied",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        workspace_encoded = base64.b64encode(str(tmp_path).encode()).decode()
        mock_sts = {
            "metadata": {
                "annotations": {
                    "paude.io/workspace": workspace_encoded,
                },
            },
        }
        with patch.object(backend, "_get_statefulset", return_value=mock_sts):
            backend.sync_session("test-session", direction="remote")

        captured = capsys.readouterr()
        assert "FAILED" in captured.err
        assert "Permission denied" in captured.err

    @patch("subprocess.run")
    def test_sync_session_exec_uses_extended_timeout(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """sync_session exec calls use OC_EXEC_TIMEOUT (not default)."""
        import base64

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            # Check for pod status request and return "Running"
            cmd = args[0] if args else []
            if "get" in cmd and "pod" in cmd and "jsonpath" in str(cmd):
                return MagicMock(returncode=0, stdout="Running", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        workspace_encoded = base64.b64encode(str(tmp_path).encode()).decode()
        mock_sts = {
            "metadata": {
                "annotations": {
                    "paude.io/workspace": workspace_encoded,
                },
            },
        }
        with patch.object(backend, "_get_statefulset", return_value=mock_sts):
            backend.sync_session("test-session", direction="remote")

        # Find all exec calls (mkdir/chmod operations)
        exec_calls = [
            c for c in mock_run.call_args_list
            if len(c[0]) > 0 and "exec" in c[0][0]
        ]

        # There should be at least 2 exec calls:
        # 1. mkdir + chmod for workspace prep
        # 2. chmod after rsync
        assert len(exec_calls) >= 2, f"Expected at least 2 exec calls, got {len(exec_calls)}"

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
        """_sync_config_to_pod uses rsync with CLAUDE_EXCLUDES for ~/.claude/."""
        # Create mock claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend._sync_config_to_pod("test-pod-0")

        # Find rsync calls
        rsync_calls = [
            c for c in mock_run.call_args_list
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
        """_sync_config_to_pod calls _rewrite_plugin_paths after rsync."""
        # Create mock claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{}")

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            with patch.object(backend, "_rewrite_plugin_paths") as mock_rewrite:
                backend._sync_config_to_pod("test-pod-0")
                mock_rewrite.assert_called_once_with("test-pod-0", "/pvc/config")

    @patch("subprocess.run")
    def test_sync_config_handles_missing_claude_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod handles missing ~/.claude/ gracefully."""
        # Don't create claude directory

        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))

        with patch("pathlib.Path.home", return_value=tmp_path):
            # Should not raise
            backend._sync_config_to_pod("test-pod-0")

        # Should not have rsync calls for claude directory
        rsync_calls = [
            c for c in mock_run.call_args_list
            if c[0] and len(c[0]) > 0 and "rsync" in c[0][0] and ".claude" in str(c)
        ]
        assert len(rsync_calls) == 0

    @patch("subprocess.run")
    def test_sync_config_skips_rewrite_on_rsync_failure(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """_sync_config_to_pod does NOT call _rewrite_plugin_paths when rsync fails."""
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
            with patch.object(backend, "_rewrite_plugin_paths") as mock_rewrite:
                backend._sync_config_to_pod("test-pod-0")
                # Should NOT be called because rsync failed
                mock_rewrite.assert_not_called()

    @patch("subprocess.run")
    def test_sync_config_prints_warning_on_rsync_failure(
        self, mock_run: MagicMock, tmp_path: Path, capsys: Any
    ) -> None:
        """_sync_config_to_pod prints warning when rsync fails."""
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
            backend._sync_config_to_pod("test-pod-0")

        captured = capsys.readouterr()
        assert "Warning: Failed to sync ~/.claude/" in captured.err
        assert "plugins may not work" in captured.err


class TestRewritePluginPaths:
    """Tests for _rewrite_plugin_paths method."""

    @patch("subprocess.run")
    def test_rewrite_plugin_paths_uses_jq(self, mock_run: MagicMock) -> None:
        """_rewrite_plugin_paths uses jq to rewrite installed_plugins.json."""
        def mock_run_side_effect(*args: Any, **kwargs: Any) -> MagicMock:
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_run_side_effect

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._rewrite_plugin_paths("test-pod-0", "/pvc/config")

        # Find exec calls with jq
        jq_calls = [
            c for c in mock_run.call_args_list
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

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._rewrite_plugin_paths("test-pod-0", "/pvc/config")

        # Check for installed_plugins.json rewrite
        installed_plugins_calls = [
            c for c in mock_run.call_args_list
            if "installed_plugins.json" in str(c)
        ]
        assert len(installed_plugins_calls) >= 1

        # Check for known_marketplaces.json rewrite
        known_marketplaces_calls = [
            c for c in mock_run.call_args_list
            if "known_marketplaces.json" in str(c)
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

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._rewrite_plugin_paths("test-pod-0", "/pvc/config")

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

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._rewrite_plugin_paths("test-pod-0", "/pvc/config")

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

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend._rewrite_plugin_paths("test-pod-0", "/pvc/config")

        # The jq expression for known_marketplaces should include null-safety
        all_calls_str = str(mock_run.call_args_list)
        assert "if .value.installLocation then" in all_calls_str


class TestClaudeExcludes:
    """Tests for CLAUDE_EXCLUDES constant."""

    def test_claude_excludes_contains_expected_patterns(self) -> None:
        """CLAUDE_EXCLUDES contains session-specific and cache patterns."""
        from paude.backends.openshift import CLAUDE_EXCLUDES

        # Session-specific patterns (anchored with leading /)
        assert "/history.jsonl" in CLAUDE_EXCLUDES
        assert "/tasks" in CLAUDE_EXCLUDES
        assert "/todos" in CLAUDE_EXCLUDES
        assert "/session-env" in CLAUDE_EXCLUDES

        # Cache patterns (anchored to only match top-level cache)
        assert "/cache" in CLAUDE_EXCLUDES
        assert "/stats-cache.json" in CLAUDE_EXCLUDES

        # Git metadata
        assert "/.git" in CLAUDE_EXCLUDES

    def test_claude_excludes_uses_anchored_patterns(self) -> None:
        """CLAUDE_EXCLUDES uses anchored patterns to not exclude plugins/cache."""
        from paude.backends.openshift import CLAUDE_EXCLUDES

        # All patterns should be anchored (start with /) to prevent
        # accidentally excluding nested directories like plugins/cache
        for pattern in CLAUDE_EXCLUDES:
            assert pattern.startswith("/"), (
                f"Pattern '{pattern}' should be anchored with leading / "
                "to prevent excluding nested directories"
            )

    def test_claude_excludes_does_not_contain_plugins(self) -> None:
        """CLAUDE_EXCLUDES does not exclude plugins directory."""
        from paude.backends.openshift import CLAUDE_EXCLUDES

        assert "plugins" not in CLAUDE_EXCLUDES
