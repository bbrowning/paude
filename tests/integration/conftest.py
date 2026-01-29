"""Pytest fixtures and configuration for integration tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Default test images - can be overridden via environment variables
DEFAULT_PODMAN_IMAGE = "paude-base-centos9:latest"
DEFAULT_K8S_IMAGE = "quay.io/bbrowning/paude-base-centos9:latest"


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for integration tests."""
    config.addinivalue_line(
        "markers",
        "integration: integration tests requiring real infrastructure",
    )
    config.addinivalue_line(
        "markers",
        "podman: tests requiring real podman installation",
    )
    config.addinivalue_line(
        "markers",
        "kubernetes: tests requiring kubernetes cluster (Kind or OpenShift)",
    )


@pytest.fixture(scope="session")
def has_podman() -> bool:
    """Check if podman is available on the system."""
    return shutil.which("podman") is not None


@pytest.fixture(scope="session")
def has_oc() -> bool:
    """Check if oc CLI is available on the system."""
    return shutil.which("oc") is not None


@pytest.fixture(scope="session")
def has_kubectl() -> bool:
    """Check if kubectl is available on the system."""
    return shutil.which("kubectl") is not None


@pytest.fixture(scope="session")
def podman_available(has_podman: bool) -> bool:
    """Check if podman is available and working."""
    if not has_podman:
        return False

    try:
        result = subprocess.run(
            ["podman", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="session")
def kubernetes_available(has_oc: bool, has_kubectl: bool) -> bool:
    """Check if a Kubernetes cluster is accessible."""
    # Prefer oc, fall back to kubectl
    cli = "oc" if has_oc else "kubectl" if has_kubectl else None
    if cli is None:
        return False

    try:
        result = subprocess.run(
            [cli, "cluster-info"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="session")
def test_image_available(podman_available: bool) -> bool:
    """Check if the test image is available locally."""
    if not podman_available:
        return False

    try:
        result = subprocess.run(
            ["podman", "image", "exists", "paude-base-centos9:latest"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture
def require_podman(podman_available: bool) -> None:
    """Skip test if podman is not available."""
    if not podman_available:
        pytest.skip("podman not available")


@pytest.fixture
def require_kubernetes(kubernetes_available: bool) -> None:
    """Skip test if kubernetes cluster is not available."""
    if not kubernetes_available:
        pytest.skip("kubernetes cluster not available")


@pytest.fixture
def require_test_image(test_image_available: bool) -> None:
    """Skip test if test image is not built."""
    if not test_image_available:
        pytest.skip("test image not available (run 'make build' first)")


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory with a fake git repo."""
    workspace = tmp_path / "test-workspace"
    workspace.mkdir()
    # Create a fake git repo so tests don't trigger "empty workspace" messages
    git_dir = workspace / ".git"
    git_dir.mkdir()
    return workspace


@pytest.fixture
def unique_session_name() -> str:
    """Generate a unique session name for testing."""
    import secrets

    return f"test-{secrets.token_hex(4)}"


@pytest.fixture(scope="session")
def podman_test_image() -> str:
    """Get the Podman test image name.

    Can be overridden with PAUDE_TEST_IMAGE environment variable.
    """
    return os.environ.get("PAUDE_TEST_IMAGE", DEFAULT_PODMAN_IMAGE)


@pytest.fixture(scope="session")
def kubernetes_test_image() -> str:
    """Get the Kubernetes test image name.

    Can be overridden with PAUDE_K8S_TEST_IMAGE environment variable.
    For CI with Kind, set this to the local image name that was loaded.
    """
    return os.environ.get("PAUDE_K8S_TEST_IMAGE", DEFAULT_K8S_IMAGE)
