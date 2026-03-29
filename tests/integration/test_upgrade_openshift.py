"""Integration tests for paude upgrade on OpenShift/Kubernetes backend."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import SessionConfig
from paude.backends.openshift.backend import OpenShiftBackend
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_YOLO,
)
from paude.cli.upgrade import UpgradeOverrides

from .conftest import run_oc

pytestmark = [pytest.mark.integration, pytest.mark.kubernetes]

OLD_VERSION = "0.12.0"


@pytest.fixture(autouse=True)
def _cleanup(cleanup_k8s_test_resources):
    """Activate shared K8s cleanup for every test in this module."""
    pass


class TestOpenShiftUpgrade:
    """Test upgrade preserves volume content and session labels."""

    def test_upgrade_preserves_volume_and_labels(
        self,
        require_kubernetes: None,
        openshift_backend: OpenShiftBackend,
        test_namespace: str,
        temp_workspace: Path,
        unique_session_name: str,
        kubernetes_test_image: str,
    ) -> None:
        """Upgrade patches StatefulSet but keeps PVC data and labels."""
        from paude import __version__ as current_version

        sts_name = f"paude-{unique_session_name}"
        pod_name = f"{sts_name}-0"

        # 1. Create session with simulated old version
        with patch("paude.__version__", OLD_VERSION):
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=kubernetes_test_image,
                allowed_domains=["redhat.com"],
                yolo=True,
                agent="gemini",
                wait_for_ready=False,
            )
            openshift_backend.create_session(config)

        # 2. Scale up and wait for pod to be ready
        run_oc(
            "scale",
            "statefulset",
            sts_name,
            "-n",
            test_namespace,
            "--replicas=1",
        )
        run_oc(
            "wait",
            "--for=condition=Ready",
            f"pod/{pod_name}",
            "-n",
            test_namespace,
            "--timeout=120s",
        )

        # 3. Write marker file to PVC
        run_oc(
            "exec",
            pod_name,
            "-n",
            test_namespace,
            "--",
            "sh",
            "-c",
            "mkdir -p /pvc/workspace && echo upgrade-test > /pvc/workspace/marker.txt",
        )

        # Verify old version label before upgrade
        result = run_oc(
            "get",
            "statefulset",
            sts_name,
            "-n",
            test_namespace,
            "-o",
            "json",
        )
        sts_json = json.loads(result.stdout)
        pre_labels = sts_json.get("metadata", {}).get("labels", {})
        assert pre_labels.get(PAUDE_LABEL_VERSION) == OLD_VERSION

        # 4. Run upgrade with mocked image building and config sync
        with (
            patch.object(
                openshift_backend,
                "ensure_image_via_build",
                return_value=kubernetes_test_image,
            ),
            patch.object(openshift_backend, "_syncer_instance", MagicMock()),
        ):
            from paude.cli.upgrade import _upgrade_openshift

            _upgrade_openshift(
                unique_session_name,
                openshift_backend,
                rebuild=False,
                openshift_context=None,
                overrides=UpgradeOverrides(),
            )

        # 5. Verify pod is running
        run_oc(
            "wait",
            "--for=condition=Ready",
            f"pod/{pod_name}",
            "-n",
            test_namespace,
            "--timeout=120s",
        )

        # 6. Verify version label updated
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
        assert labels.get(PAUDE_LABEL_VERSION) == current_version
        assert labels.get(PAUDE_LABEL_AGENT) == "gemini"
        assert labels.get(PAUDE_LABEL_YOLO) == "1"

        # 7. Verify marker file survived the upgrade
        result = run_oc(
            "exec",
            pod_name,
            "-n",
            test_namespace,
            "--",
            "cat",
            "/pvc/workspace/marker.txt",
        )
        assert "upgrade-test" in result.stdout
