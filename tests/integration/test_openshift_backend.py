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

    # Delete all labeled resources in one call
    run_oc(
        "delete",
        "statefulset,networkpolicy,deployment,service",
        "-n",
        test_namespace,
        "-l",
        f"paude.io/session-name={unique_session_name}",
        "--ignore-not-found",
        check=False,
    )

    # PVC needs separate deletion (created by volumeClaimTemplate, may not have session label)
    sts_name = f"paude-{unique_session_name}"
    pvc_name = f"workspace-{sts_name}-0"
    run_oc(
        "delete",
        "pvc",
        pvc_name,
        "-n",
        test_namespace,
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
            wait_for_ready=False,
        )

        session = openshift_backend.create_session(config)

        assert session.name == unique_session_name
        assert session.status == "pending"
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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
            wait_for_ready=False,
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


class TestProxyDeployment:
    """Test proxy container deployment with allowed_domains."""

    def test_proxy_starts_with_allowed_domains(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Proxy container starts successfully with allowed_domains configured.

        This catches container-level bugs like squid.conf syntax errors
        (e.g. referencing ACLs before they are defined).
        """
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=kubernetes_test_image,
            allowed_domains=["example.com", ".googleapis.com"],
        )

        openshift_backend.create_session(config)

        proxy_name = f"paude-proxy-{unique_session_name}"

        # Poll for proxy readiness (create_session waits too, but give extra time)
        import time

        ready_replicas = 0
        for _ in range(5):
            result = run_oc(
                "get",
                "deployment",
                proxy_name,
                "-n",
                test_namespace,
                "-o",
                "jsonpath={.status.readyReplicas}",
                check=False,
            )
            ready_replicas = int(result.stdout.strip() or "0")
            if ready_replicas > 0:
                break
            time.sleep(2)

        if ready_replicas == 0:
            # Collect diagnostics before failing
            diag_lines = [f"Proxy deployment {proxy_name} has no ready replicas."]

            # Pod events
            events = run_oc(
                "get",
                "events",
                "-n",
                test_namespace,
                "--field-selector",
                f"involvedObject.name={proxy_name}",
                "--sort-by=.lastTimestamp",
                check=False,
            )
            if events.stdout.strip():
                diag_lines.append(
                    f"\n=== Deployment Events ===\n{events.stdout.strip()}"
                )

            # Find proxy pod(s) and get their events/logs
            pods = run_oc(
                "get",
                "pods",
                "-n",
                test_namespace,
                "-l",
                f"app=paude-proxy,paude.io/session-name={unique_session_name}",
                "-o",
                "jsonpath={.items[*].metadata.name}",
                check=False,
            )
            for pod_name in pods.stdout.strip().split():
                if not pod_name:
                    continue
                pod_events = run_oc(
                    "get",
                    "events",
                    "-n",
                    test_namespace,
                    "--field-selector",
                    f"involvedObject.name={pod_name}",
                    "--sort-by=.lastTimestamp",
                    check=False,
                )
                if pod_events.stdout.strip():
                    diag_lines.append(
                        f"\n=== Events for {pod_name} ===\n{pod_events.stdout.strip()}"
                    )
                pod_logs = run_oc(
                    "logs",
                    pod_name,
                    "-n",
                    test_namespace,
                    "--tail=50",
                    check=False,
                )
                if pod_logs.stdout.strip():
                    diag_lines.append(
                        f"\n=== Logs for {pod_name} ===\n{pod_logs.stdout.strip()}"
                    )
                if pod_logs.stderr.strip():
                    diag_lines.append(
                        f"\n=== Stderr for {pod_name} ===\n{pod_logs.stderr.strip()}"
                    )
                # Read squid log files from the container
                for log_file in ["/tmp/squid-cache.log", "/tmp/squid-blocked.log"]:
                    squid_log = run_oc(
                        "exec",
                        pod_name,
                        "-n",
                        test_namespace,
                        "--",
                        "cat",
                        log_file,
                        check=False,
                    )
                    if squid_log.stdout.strip():
                        diag_lines.append(
                            f"\n=== {log_file} from {pod_name} ===\n{squid_log.stdout.strip()}"
                        )
                pod_describe = run_oc(
                    "describe",
                    "pod",
                    pod_name,
                    "-n",
                    test_namespace,
                    check=False,
                )
                if pod_describe.stdout.strip():
                    desc_lines = pod_describe.stdout.strip().split("\n")
                    truncated = "\n".join(desc_lines[:60])
                    diag_lines.append(
                        f"\n=== Describe {pod_name} (truncated) ===\n{truncated}"
                    )

            pytest.fail("\n".join(diag_lines))

        # Verify ALLOWED_DOMAINS env var on the Deployment
        result = run_oc(
            "get",
            "deployment",
            proxy_name,
            "-n",
            test_namespace,
            "-o",
            "jsonpath={.spec.template.spec.containers[0].env[0].value}",
        )
        assert result.stdout.strip() == "example.com,.googleapis.com"

        # Verify proxy Service exists
        assert resource_exists("service", proxy_name, test_namespace)

        # Get the proxy pod name
        result = run_oc(
            "get",
            "pods",
            "-n",
            test_namespace,
            "-l",
            f"app=paude-proxy,paude.io/session-name={unique_session_name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        )
        pod_name = result.stdout.strip()
        assert pod_name, "Could not find proxy pod"

        # Read squid.conf from the running proxy container
        result = run_oc(
            "exec",
            pod_name,
            "-n",
            test_namespace,
            "--",
            "cat",
            "/tmp/squid.conf",
        )
        squid_conf = result.stdout

        # Verify ACL entries for both test domains
        assert "example.com" in squid_conf
        assert ".googleapis.com" in squid_conf

        # Verify the access_log directive that triggered the original bug
        assert "!allowed_domains" in squid_conf
