"""Tests for OpenShift resource builders."""

from __future__ import annotations

from paude.backends.openshift.resources import StatefulSetBuilder


def _build_statefulset(gpu: str | None = None) -> dict:
    """Build a StatefulSet spec with default test settings."""
    builder = StatefulSetBuilder(
        session_name="test-session",
        namespace="test-ns",
        image="test:latest",
        resources={
            "requests": {"cpu": "1", "memory": "2Gi"},
            "limits": {"cpu": "2", "memory": "4Gi"},
        },
        gpu=gpu,
    )
    return builder.with_env({}).build()


class TestStatefulSetBuilderSecurity:
    """Tests for security hardening in StatefulSetBuilder."""

    def test_pod_spec_security_defaults(self):
        """Service account token and service links are disabled."""
        spec = _build_statefulset()
        pod_spec = spec["spec"]["template"]["spec"]
        assert pod_spec["automountServiceAccountToken"] is False
        assert pod_spec["enableServiceLinks"] is False


class TestStatefulSetBuilderGpu:
    """Tests for GPU support in StatefulSetBuilder."""

    def _get_container_resources(self, spec: dict) -> dict:
        """Extract container resources from a StatefulSet spec."""
        containers = spec["spec"]["template"]["spec"]["containers"]
        return containers[0]["resources"]

    def test_no_gpu_when_none(self):
        """No nvidia.com/gpu in resources when gpu is None."""
        spec = _build_statefulset(gpu=None)
        resources = self._get_container_resources(spec)
        assert "nvidia.com/gpu" not in resources["requests"]
        assert "nvidia.com/gpu" not in resources["limits"]

    def test_gpu_all_requests_one(self):
        """gpu='all' adds nvidia.com/gpu: '1' to resources."""
        spec = _build_statefulset(gpu="all")
        resources = self._get_container_resources(spec)
        assert resources["requests"]["nvidia.com/gpu"] == "1"
        assert resources["limits"]["nvidia.com/gpu"] == "1"

    def test_gpu_device_spec_counts_devices(self):
        """gpu='device=0,1' adds nvidia.com/gpu: '2'."""
        spec = _build_statefulset(gpu="device=0,1")
        resources = self._get_container_resources(spec)
        assert resources["requests"]["nvidia.com/gpu"] == "2"
        assert resources["limits"]["nvidia.com/gpu"] == "2"

    def test_gpu_single_device(self):
        """gpu='device=0' adds nvidia.com/gpu: '1'."""
        spec = _build_statefulset(gpu="device=0")
        resources = self._get_container_resources(spec)
        assert resources["requests"]["nvidia.com/gpu"] == "1"

    def test_gpu_numeric_string(self):
        """gpu='3' adds nvidia.com/gpu: '3'."""
        spec = _build_statefulset(gpu="3")
        resources = self._get_container_resources(spec)
        assert resources["requests"]["nvidia.com/gpu"] == "3"
        assert resources["limits"]["nvidia.com/gpu"] == "3"

    def test_gpu_preserves_other_resources(self):
        """GPU resources don't overwrite existing CPU/memory resources."""
        spec = _build_statefulset(gpu="all")
        resources = self._get_container_resources(spec)
        assert resources["requests"]["cpu"] == "1"
        assert resources["requests"]["memory"] == "2Gi"
        assert resources["limits"]["cpu"] == "2"
        assert resources["limits"]["memory"] == "4Gi"
