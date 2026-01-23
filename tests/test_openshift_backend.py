"""Tests for the OpenShift backend module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
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


class TestGeneratePodSpec:
    """Tests for _generate_pod_spec method."""

    def test_generates_valid_spec(self) -> None:
        """_generate_pod_spec generates valid pod spec."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={"KEY": "value"},
        )

        assert spec["apiVersion"] == "v1"
        assert spec["kind"] == "Pod"
        assert spec["metadata"]["name"] == "paude-session-test-123"
        assert spec["metadata"]["labels"]["app"] == "paude"
        assert spec["metadata"]["labels"]["session-id"] == "test-123"

    def test_includes_environment_variables(self) -> None:
        """_generate_pod_spec includes environment variables."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={"KEY1": "value1", "KEY2": "value2"},
        )

        container = spec["spec"]["containers"][0]
        env_list = container["env"]
        env_dict = {e["name"]: e["value"] for e in env_list}

        assert env_dict["KEY1"] == "value1"
        assert env_dict["KEY2"] == "value2"

    def test_uses_config_namespace(self) -> None:
        """_generate_pod_spec uses namespace from config."""
        config = OpenShiftConfig(namespace="custom-ns")
        backend = OpenShiftBackend(config=config)
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
        )

        assert spec["metadata"]["namespace"] == "custom-ns"

    def test_includes_resources(self) -> None:
        """_generate_pod_spec includes resource requests and limits."""
        config = OpenShiftConfig(
            namespace="test-ns",
            resources={
                "requests": {"cpu": "2", "memory": "8Gi"},
                "limits": {"cpu": "8", "memory": "16Gi"},
            },
        )
        backend = OpenShiftBackend(config=config)
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
        )

        container = spec["spec"]["containers"][0]
        assert container["resources"]["requests"]["cpu"] == "2"
        assert container["resources"]["limits"]["memory"] == "16Gi"

    def test_includes_workload_labels(self) -> None:
        """_generate_pod_spec includes labels for network policy."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
        )

        labels = spec["metadata"]["labels"]
        assert labels["role"] == "workload"
        assert labels["app"] == "paude"


class TestListSessions:
    """Tests for list_sessions method."""

    @patch("subprocess.run")
    def test_returns_empty_on_error(self, mock_run: MagicMock) -> None:
        """list_sessions returns empty list on error."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        sessions = backend.list_sessions()

        assert sessions == []

    @patch("subprocess.run")
    def test_parses_pods_response_legacy(self, mock_run: MagicMock) -> None:
        """list_sessions_legacy parses pods response correctly."""
        pods_response = {
            "items": [
                {
                    "metadata": {
                        "name": "paude-session-abc123",
                        "labels": {"session-id": "abc123"},
                        "creationTimestamp": "2024-01-15T10:00:00Z",
                    },
                    "status": {"phase": "Running"},
                },
                {
                    "metadata": {
                        "name": "paude-session-def456",
                        "labels": {"session-id": "def456"},
                        "creationTimestamp": "2024-01-15T11:00:00Z",
                    },
                    "status": {"phase": "Pending"},
                },
            ]
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(pods_response),
            stderr="",
        )

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        sessions = backend.list_sessions_legacy()

        assert len(sessions) == 2
        assert sessions[0].name == "abc123"
        assert sessions[0].status == "running"
        assert sessions[1].name == "def456"
        assert sessions[1].status == "pending"


class TestStopSession:
    """Tests for stop_session_legacy method."""

    @patch("subprocess.run")
    def test_deletes_pod(self, mock_run: MagicMock) -> None:
        """stop_session_legacy deletes the pod."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.stop_session_legacy("abc123")

        # Should have called oc delete pod and oc delete secrets
        assert mock_run.call_count >= 2
        calls = [call[0][0] for call in mock_run.call_args_list]

        # First call should be delete pod
        assert "delete" in calls[0]
        assert "pod" in calls[0]


class TestAttachSession:
    """Tests for attach_session_legacy method."""

    @patch("subprocess.run")
    def test_returns_error_when_pod_not_found(self, mock_run: MagicMock) -> None:
        """attach_session_legacy returns 1 when pod not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend.attach_session_legacy("nonexistent")

        assert result == 1

    @patch("subprocess.run")
    def test_returns_error_when_pod_not_running(self, mock_run: MagicMock) -> None:
        """attach_session_legacy returns 1 when pod not running."""
        # First call is get pod status, second would be exec
        mock_run.return_value = MagicMock(returncode=0, stdout="Pending", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend.attach_session_legacy("pending-session")

        assert result == 1


class TestSyncWorkspace:
    """Tests for sync_workspace method."""

    @patch("subprocess.run")
    def test_sync_verifies_pod_running(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sync_workspace verifies pod is running before syncing."""
        # Return "Pending" for pod phase check
        mock_run.return_value = MagicMock(returncode=0, stdout="Pending", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.sync_workspace("abc123", "both")

        captured = capsys.readouterr()
        assert "not running" in captured.err

    @patch("subprocess.run")
    def test_sync_handles_missing_pod(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """sync_workspace handles missing pod gracefully."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.sync_workspace("abc123", "both")

        captured = capsys.readouterr()
        assert "not found" in captured.err


class TestGenerateSessionId:
    """Tests for _generate_session_id method."""

    def test_format(self) -> None:
        """Session ID has expected format."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        session_id = backend._generate_session_id()

        # Should be timestamp-hex format
        parts = session_id.split("-")
        assert len(parts) == 2
        assert parts[0].isdigit()
        assert len(parts[1]) == 8  # 4 bytes = 8 hex chars

    def test_unique(self) -> None:
        """Session IDs are unique."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        ids = [backend._generate_session_id() for _ in range(10)]
        assert len(set(ids)) == 10


class TestPodSpecWithCredentials:
    """Tests for _generate_pod_spec with credential mounts."""

    def test_includes_gcloud_secret_mount(self) -> None:
        """Pod spec includes gcloud secret mount when provided."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
            gcloud_secret="paude-gcloud-test-123",  # noqa: S106
        )

        container = spec["spec"]["containers"][0]
        mounts = container["volumeMounts"]

        gcloud_mount = next(
            (m for m in mounts if m["name"] == "gcloud-creds"), None
        )
        assert gcloud_mount is not None
        assert gcloud_mount["mountPath"] == "/home/paude/.config/gcloud"
        assert gcloud_mount["readOnly"] is True

        volumes = spec["spec"]["volumes"]
        gcloud_vol = next((v for v in volumes if v["name"] == "gcloud-creds"), None)
        assert gcloud_vol is not None
        assert gcloud_vol["secret"]["secretName"] == "paude-gcloud-test-123"

    def test_includes_gitconfig_configmap_mount(self) -> None:
        """Pod spec includes gitconfig mount when provided."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
            gitconfig_cm="paude-gitconfig-test-123",
        )

        container = spec["spec"]["containers"][0]
        mounts = container["volumeMounts"]

        git_mount = next((m for m in mounts if m["name"] == "gitconfig"), None)
        assert git_mount is not None
        assert git_mount["mountPath"] == "/home/paude/.gitconfig"
        assert git_mount["subPath"] == ".gitconfig"

    def test_includes_claude_secret_mount(self) -> None:
        """Pod spec includes claude secret mount when provided."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
            claude_secret="paude-claude-test-123",  # noqa: S106
        )

        container = spec["spec"]["containers"][0]
        mounts = container["volumeMounts"]

        claude_mount = next(
            (m for m in mounts if m["name"] == "claude-config"), None
        )
        assert claude_mount is not None
        assert claude_mount["mountPath"] == "/tmp/claude.seed"

    def test_no_extra_mounts_when_no_credentials(self) -> None:
        """Pod spec has only workspace mount when no credentials."""
        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        spec = backend._generate_pod_spec(
            session_id="test-123",
            image="paude:latest",
            env={},
        )

        container = spec["spec"]["containers"][0]
        mounts = container["volumeMounts"]
        volumes = spec["spec"]["volumes"]

        # Only workspace mount
        assert len(mounts) == 1
        assert mounts[0]["name"] == "workspace"
        assert len(volumes) == 1
        assert volumes[0]["name"] == "workspace"


class TestCreateCredentialsSecret:
    """Tests for _create_credentials_secret method."""

    @patch("subprocess.run")
    def test_returns_none_when_no_gcloud_dir(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns None when gcloud directory doesn't exist."""
        with patch("pathlib.Path.home", return_value=tmp_path):
            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            result = backend._create_credentials_secret("test-123")
            assert result is None

    @patch("subprocess.run")
    def test_creates_secret_with_adc(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Creates secret when ADC file exists."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake gcloud directory with ADC
        gcloud_dir = tmp_path / ".config" / "gcloud"
        gcloud_dir.mkdir(parents=True)
        (gcloud_dir / "application_default_credentials.json").write_text('{"test": true}')

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            result = backend._create_credentials_secret("test-123")

            assert result == "paude-gcloud-test-123"
            mock_run.assert_called()


class TestCreateGitconfigConfigmap:
    """Tests for _create_gitconfig_configmap method."""

    @patch("subprocess.run")
    def test_returns_none_when_no_gitconfig(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Returns None when .gitconfig doesn't exist."""
        with patch("pathlib.Path.home", return_value=tmp_path):
            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            result = backend._create_gitconfig_configmap("test-123")
            assert result is None

    @patch("subprocess.run")
    def test_creates_configmap_when_gitconfig_exists(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Creates ConfigMap when .gitconfig exists."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Create fake gitconfig
        (tmp_path / ".gitconfig").write_text("[user]\n  name = Test\n")

        with patch("pathlib.Path.home", return_value=tmp_path):
            backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
            result = backend._create_gitconfig_configmap("test-123")

            assert result == "paude-gitconfig-test-123"
            mock_run.assert_called()


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
