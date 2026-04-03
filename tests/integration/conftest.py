"""Pytest fixtures and configuration for integration tests."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from paude.backends.base import Session, SessionConfig
from paude.backends.openshift.backend import OpenShiftBackend
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.podman import PodmanBackend, SessionNotFoundError

# Default test images - can be overridden via environment variables
DEFAULT_PODMAN_IMAGE = "paude-base-centos10:latest"
DEFAULT_K8S_IMAGE = "quay.io/bbrowning/paude-base-centos10:latest"


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
            ["podman", "image", "exists", "paude-base-centos10:latest"],
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


@pytest.fixture(scope="class")
def temp_workspace_class(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a temporary workspace directory with a fake git repo (class-scoped)."""
    workspace = tmp_path_factory.mktemp("test-workspace")
    git_dir = workspace / ".git"
    git_dir.mkdir()
    return workspace


@pytest.fixture
def unique_session_name() -> str:
    """Generate a unique session name for testing."""
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


DEFAULT_PROXY_IMAGE = "paude-proxy-centos10:latest"


@pytest.fixture(scope="session")
def proxy_image_available(podman_available: bool) -> bool:
    """Check if the proxy image is available locally."""
    if not podman_available:
        return False

    try:
        result = subprocess.run(
            ["podman", "image", "exists", DEFAULT_PROXY_IMAGE],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture
def require_proxy_image(proxy_image_available: bool) -> None:
    """Skip test if proxy image is not built."""
    if not proxy_image_available:
        pytest.skip("proxy image not available (run 'make build' first)")


@pytest.fixture(scope="session")
def podman_proxy_image() -> str:
    """Get the Podman proxy image name.

    Can be overridden with PAUDE_PROXY_IMAGE environment variable.
    """
    return os.environ.get("PAUDE_PROXY_IMAGE", DEFAULT_PROXY_IMAGE)


@pytest.fixture(scope="session", autouse=True)
def shorter_pod_timeout() -> None:
    """Set a shorter pod ready timeout for integration tests.

    Uses 60 seconds instead of the default 300 seconds to fail faster
    in CI when pods have issues like ImagePullBackOff.
    """
    # Only set if not already configured
    if "PAUDE_POD_READY_TIMEOUT" not in os.environ:
        os.environ["PAUDE_POD_READY_TIMEOUT"] = "60"


# ---------------------------------------------------------------------------
# Shared OpenShift/Kubernetes helpers and fixtures
# ---------------------------------------------------------------------------


def run_oc(
    *args: str, check: bool = True, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    """Run an oc command and return the result."""
    result = subprocess.run(
        ["oc", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
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


def wait_for_resource(
    kind: str,
    name: str,
    namespace: str | None = None,
    timeout: int = 30,
    interval: float = 2,
) -> bool:
    """Poll until a Kubernetes resource exists, or timeout is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if resource_exists(kind, name, namespace):
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope="session")
def test_namespace(kubernetes_available: bool) -> str:
    """Get or create a test namespace."""
    if not kubernetes_available:
        pytest.skip("kubernetes not available")

    namespace = "paude-integration-test"

    if not resource_exists("namespace", namespace):
        run_oc("create", "namespace", namespace)

    return namespace


@pytest.fixture
def openshift_backend(test_namespace: str) -> OpenShiftBackend:
    """Create an OpenShift backend configured for the test namespace."""
    config = OpenShiftConfig(namespace=test_namespace)
    return OpenShiftBackend(config)


@pytest.fixture(autouse=False)
def cleanup_k8s_test_resources(test_namespace: str, unique_session_name: str):
    """Clean up Kubernetes test resources after each test.

    Not autouse — test modules must request it explicitly or mark it autouse
    via their own fixture.
    """
    yield

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


# ---------------------------------------------------------------------------
# Helpers used by both fixtures and tests
# ---------------------------------------------------------------------------


def cleanup_session(backend: PodmanBackend, session_name: str) -> None:
    """Clean up a session, ignoring errors if it doesn't exist."""
    try:
        backend.delete_session(session_name, confirm=True)
    except SessionNotFoundError:
        pass
    except Exception:
        # Also try direct podman cleanup as fallback
        subprocess.run(
            ["podman", "rm", "-f", f"paude-{session_name}"],
            capture_output=True,
        )
        subprocess.run(
            ["podman", "volume", "rm", "-f", f"paude-{session_name}-workspace"],
            capture_output=True,
        )

    # Always clean up proxy container and network (may exist from proxy tests)
    subprocess.run(
        ["podman", "rm", "-f", f"paude-proxy-{session_name}"],
        capture_output=True,
    )
    subprocess.run(
        ["podman", "network", "rm", "-f", f"paude-net-{session_name}"],
        capture_output=True,
    )


def _start_proxy_session(
    backend: PodmanBackend,
    session_name: str,
    workspace: Path,
    main_image: str,
    proxy_image: str,
    allowed_domains: list[str],
) -> str:
    """Create and start a session with proxy egress filtering.

    Returns the proxy container's IP address on the internal network.
    """
    config = SessionConfig(
        name=session_name,
        workspace=workspace,
        image=main_image,
        allowed_domains=allowed_domains,
        proxy_image=proxy_image,
    )
    backend.create_session(config)
    backend.start_session_no_attach(session_name)
    time.sleep(1)  # Extra time for proxy to fully initialize

    # Get proxy IP on the internal network
    proxy_name = f"paude-proxy-{session_name}"
    network_name = f"paude-net-{session_name}"
    result = subprocess.run(
        [
            "podman",
            "inspect",
            "--format",
            f'{{{{(index .NetworkSettings.Networks "{network_name}").IPAddress}}}}',
            proxy_name,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Class-scoped fixtures for shared sessions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def stopped_session(
    podman_available: bool,
    test_image_available: bool,
    proxy_image_available: bool,
    temp_workspace_class: Path,
    podman_test_image: str,
    podman_proxy_image: str,
) -> Generator[tuple[PodmanBackend, str, Session, Path], None, None]:
    """Create a stopped session once for the entire test class."""
    if not podman_available:
        pytest.skip("podman not available")
    if not test_image_available:
        pytest.skip("test image not available (run 'make build' first)")
    backend = PodmanBackend()
    name = f"test-{secrets.token_hex(4)}"
    config = SessionConfig(
        name=name,
        workspace=temp_workspace_class,
        image=podman_test_image,
        proxy_image=podman_proxy_image if proxy_image_available else None,
    )
    session = backend.create_session(config)
    yield (backend, name, session, temp_workspace_class)
    cleanup_session(backend, name)


@pytest.fixture(scope="class")
def running_session(
    podman_available: bool,
    test_image_available: bool,
    proxy_image_available: bool,
    temp_workspace_class: Path,
    podman_test_image: str,
    podman_proxy_image: str,
) -> Generator[tuple[PodmanBackend, str, Path], None, None]:
    """Create and start a session once for the entire test class."""
    if not podman_available:
        pytest.skip("podman not available")
    if not test_image_available:
        pytest.skip("test image not available (run 'make build' first)")
    backend = PodmanBackend()
    name = f"test-{secrets.token_hex(4)}"
    config = SessionConfig(
        name=name,
        workspace=temp_workspace_class,
        image=podman_test_image,
        proxy_image=podman_proxy_image if proxy_image_available else None,
    )
    backend.create_session(config)
    backend.start_session_no_attach(name)
    yield (backend, name, temp_workspace_class)
    cleanup_session(backend, name)


@pytest.fixture(scope="class")
def running_proxy_session(
    podman_available: bool,
    test_image_available: bool,
    proxy_image_available: bool,
    temp_workspace_class: Path,
    podman_test_image: str,
    podman_proxy_image: str,
) -> Generator[tuple[PodmanBackend, str, str], None, None]:
    """Create and start a proxy session once for the entire test class.

    Yields (backend, session_name, proxy_ip).
    """
    if not podman_available:
        pytest.skip("podman not available")
    if not test_image_available:
        pytest.skip("test image not available (run 'make build' first)")
    if not proxy_image_available:
        pytest.skip("proxy image not available (run 'make build' first)")
    backend = PodmanBackend()
    name = f"test-{secrets.token_hex(4)}"
    proxy_ip = _start_proxy_session(
        backend=backend,
        session_name=name,
        workspace=temp_workspace_class,
        main_image=podman_test_image,
        proxy_image=podman_proxy_image,
        allowed_domains=[".googleapis.com"],
    )
    yield (backend, name, proxy_ip)
    cleanup_session(backend, name)
