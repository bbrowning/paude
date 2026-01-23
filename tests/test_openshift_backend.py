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
        assert config.registry is None
        assert "requests" in config.resources
        assert "limits" in config.resources

    def test_custom_values(self) -> None:
        """OpenShiftConfig accepts custom values."""
        config = OpenShiftConfig(
            context="my-context",
            namespace="my-namespace",
            registry="my-registry:5000",
            resources={"requests": {"cpu": "2", "memory": "8Gi"}},
        )

        assert config.context == "my-context"
        assert config.namespace == "my-namespace"
        assert config.registry == "my-registry:5000"
        assert config.resources["requests"]["cpu"] == "2"


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
    def test_parses_pods_response(self, mock_run: MagicMock) -> None:
        """list_sessions parses pods response correctly."""
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
        sessions = backend.list_sessions()

        assert len(sessions) == 2
        assert sessions[0].id == "abc123"
        assert sessions[0].status == "running"
        assert sessions[1].id == "def456"
        assert sessions[1].status == "pending"


class TestStopSession:
    """Tests for stop_session method."""

    @patch("subprocess.run")
    def test_deletes_pod(self, mock_run: MagicMock) -> None:
        """stop_session deletes the pod."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        backend.stop_session("abc123")

        # Should have called oc delete pod and oc delete secrets
        assert mock_run.call_count >= 2
        calls = [call[0][0] for call in mock_run.call_args_list]

        # First call should be delete pod
        assert "delete" in calls[0]
        assert "pod" in calls[0]


class TestAttachSession:
    """Tests for attach_session method."""

    @patch("subprocess.run")
    def test_returns_error_when_pod_not_found(self, mock_run: MagicMock) -> None:
        """attach_session returns 1 when pod not found."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend.attach_session("nonexistent")

        assert result == 1

    @patch("subprocess.run")
    def test_returns_error_when_pod_not_running(self, mock_run: MagicMock) -> None:
        """attach_session returns 1 when pod not running."""
        # First call is get pod status, second would be exec
        mock_run.return_value = MagicMock(returncode=0, stdout="Pending", stderr="")

        backend = OpenShiftBackend(config=OpenShiftConfig(namespace="test-ns"))
        result = backend.attach_session("pending-session")

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
