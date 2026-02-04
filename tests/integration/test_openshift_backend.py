"""Integration tests for OpenShift backend with real Kubernetes cluster."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from paude.backends.base import SessionConfig
from paude.backends.openshift.backend import OpenShiftBackend
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import (
    SessionExistsError,
    SessionNotFoundError,
)

pytestmark = [pytest.mark.integration, pytest.mark.kubernetes]


def run_oc(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run an oc command and return the result."""
    result = subprocess.run(
        ["oc", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"oc {' '.join(args)} failed: {result.stderr}")
    return result


def resource_exists(kind: str, name: str, namespace: str | None = None) -> bool:
    """Check if a Kubernetes resource exists."""
    cmd = ["get", kind, name, "-o", "name"]
    if namespace:
        cmd.extend(["-n", namespace])
    result = run_oc(*cmd, check=False)
    return result.returncode == 0


@pytest.fixture(scope="session")
def test_namespace(kubernetes_available: bool) -> str:
    """Get or create a test namespace."""
    if not kubernetes_available:
        pytest.skip("kubernetes not available")

    namespace = "paude-integration-test"

    # Create namespace if it doesn't exist
    if not resource_exists("namespace", namespace):
        run_oc("create", "namespace", namespace)

    return namespace


@pytest.fixture
def openshift_backend(test_namespace: str) -> OpenShiftBackend:
    """Create an OpenShift backend configured for the test namespace."""
    config = OpenShiftConfig(namespace=test_namespace)
    return OpenShiftBackend(config)


@pytest.fixture(autouse=True)
def cleanup_test_resources(test_namespace: str, unique_session_name: str):
    """Clean up test resources after each test."""
    yield

    # Delete any resources created by the test
    sts_name = f"paude-{unique_session_name}"
    pvc_name = f"workspace-{sts_name}-0"

    # Delete StatefulSet
    run_oc(
        "delete",
        "statefulset",
        sts_name,
        "-n",
        test_namespace,
        "--ignore-not-found",
        check=False,
    )

    # Delete PVC
    run_oc(
        "delete",
        "pvc",
        pvc_name,
        "-n",
        test_namespace,
        "--ignore-not-found",
        check=False,
    )

    # Delete NetworkPolicies
    run_oc(
        "delete",
        "networkpolicy",
        "-n",
        test_namespace,
        "-l",
        f"paude.io/session-name={unique_session_name}",
        "--ignore-not-found",
        check=False,
    )


class TestOpenShiftSessionLifecycle:
    """Test complete session lifecycle on Kubernetes."""

    def test_create_session_creates_statefulset_and_pvc(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Creating a session creates StatefulSet and PVC."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        session = openshift_backend.create_session(config)

        assert session.name == unique_session_name
        assert session.backend_type == "openshift"

        # Verify StatefulSet exists
        sts_name = f"paude-{unique_session_name}"
        assert resource_exists("statefulset", sts_name, test_namespace)

        # Verify PVC exists (created by StatefulSet volumeClaimTemplate)
        pvc_name = f"workspace-{sts_name}-0"
        assert resource_exists("pvc", pvc_name, test_namespace)

        # Verify NetworkPolicy exists
        result = run_oc(
            "get",
            "networkpolicy",
            "-n",
            test_namespace,
            "-l",
            f"paude.io/session-name={unique_session_name}",
            "-o",
            "name",
            check=False,
        )
        assert result.returncode == 0
        assert "networkpolicy" in result.stdout.lower()

    def test_create_session_raises_if_exists(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Creating a session with existing name raises SessionExistsError."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        # Try to create again with same name
        with pytest.raises(SessionExistsError):
            openshift_backend.create_session(config)

    def test_delete_session_removes_resources(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Deleting a session removes StatefulSet, PVC, and NetworkPolicy."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        # Delete the session
        openshift_backend.delete_session(unique_session_name, confirm=True)

        # Verify resources are gone
        sts_name = f"paude-{unique_session_name}"
        assert not resource_exists("statefulset", sts_name, test_namespace)

        pvc_name = f"workspace-{sts_name}-0"
        assert not resource_exists("pvc", pvc_name, test_namespace)

    def test_delete_nonexistent_session_raises_error(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
    ) -> None:
        """Deleting a nonexistent session raises SessionNotFoundError."""
        with pytest.raises(SessionNotFoundError):
            openshift_backend.delete_session("nonexistent-session-xyz", confirm=True)

    def test_list_sessions_returns_created_sessions(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """List sessions includes created sessions."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        sessions = openshift_backend.list_sessions()
        session_names = [s.name for s in sessions]

        assert unique_session_name in session_names

    def test_get_session_returns_session_info(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Get session returns correct session information."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        session = openshift_backend.get_session(unique_session_name)

        assert session is not None
        assert session.name == unique_session_name
        assert session.backend_type == "openshift"

    def test_get_nonexistent_session_returns_none(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
    ) -> None:
        """Get session returns None for nonexistent session."""
        session = openshift_backend.get_session("nonexistent-session-xyz")
        assert session is None


class TestOpenShiftStatefulSetSpec:
    """Test StatefulSet specification generated by the backend."""

    def test_statefulset_has_correct_labels(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """StatefulSet has correct labels for session identification."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        sts_name = f"paude-{unique_session_name}"
        result = run_oc(
            "get",
            "statefulset",
            sts_name,
            "-n",
            test_namespace,
            "-o",
            "json",
        )

        sts = json.loads(result.stdout)
        labels = sts.get("metadata", {}).get("labels", {})

        assert labels.get("app") == "paude"
        assert labels.get("paude.io/session-name") == unique_session_name

    def test_statefulset_has_pvc_template(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """StatefulSet has volumeClaimTemplate for workspace PVC."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        sts_name = f"paude-{unique_session_name}"
        result = run_oc(
            "get",
            "statefulset",
            sts_name,
            "-n",
            test_namespace,
            "-o",
            "json",
        )

        sts = json.loads(result.stdout)
        vct = sts.get("spec", {}).get("volumeClaimTemplates", [])

        assert len(vct) >= 1
        assert vct[0].get("metadata", {}).get("name") == "workspace"


class TestOpenShiftScaling:
    """Test session start/stop via StatefulSet scaling."""

    def test_stop_session_scales_to_zero(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Stopping a session scales StatefulSet to 0 replicas."""
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
        )

        openshift_backend.create_session(config)

        # Stop the session
        openshift_backend.stop_session(unique_session_name)

        # Verify replicas is 0
        sts_name = f"paude-{unique_session_name}"
        result = run_oc(
            "get",
            "statefulset",
            sts_name,
            "-n",
            test_namespace,
            "-o",
            "jsonpath={.spec.replicas}",
        )

        assert result.stdout.strip() == "0"
