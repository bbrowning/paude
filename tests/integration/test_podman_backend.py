"""Integration tests for Podman backend with real Podman operations."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from paude.backends.base import SessionConfig
from paude.backends.podman import (
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
)

pytestmark = [pytest.mark.integration, pytest.mark.podman]


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


class TestPodmanSessionLifecycle:
    """Test complete session lifecycle with real Podman."""

    def test_create_session_creates_container_and_volume(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Creating a session creates both container and volume."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            session = backend.create_session(config)

            assert session.name == unique_session_name
            assert session.status == "stopped"
            assert session.backend_type == "podman"

            # Verify container exists
            result = subprocess.run(
                ["podman", "container", "exists", f"paude-{unique_session_name}"],
                capture_output=True,
            )
            assert result.returncode == 0, "Container should exist"

            # Verify volume exists
            result = subprocess.run(
                [
                    "podman",
                    "volume",
                    "exists",
                    f"paude-{unique_session_name}-workspace",
                ],
                capture_output=True,
            )
            assert result.returncode == 0, "Volume should exist"

        finally:
            cleanup_session(backend, unique_session_name)

    def test_create_session_raises_if_exists(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Creating a session with existing name raises SessionExistsError."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            # Try to create again with same name
            with pytest.raises(SessionExistsError):
                backend.create_session(config)

        finally:
            cleanup_session(backend, unique_session_name)

    def test_delete_session_removes_container_and_volume(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Deleting a session removes both container and volume."""
        backend = PodmanBackend()

        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=podman_test_image,
        )
        backend.create_session(config)

        # Delete the session (testing delete_session itself)
        backend.delete_session(unique_session_name, confirm=True)

        # Verify container is gone
        result = subprocess.run(
            ["podman", "container", "exists", f"paude-{unique_session_name}"],
            capture_output=True,
        )
        assert result.returncode != 0, "Container should be deleted"

        # Verify volume is gone
        result = subprocess.run(
            ["podman", "volume", "exists", f"paude-{unique_session_name}-workspace"],
            capture_output=True,
        )
        assert result.returncode != 0, "Volume should be deleted"

    def test_delete_nonexistent_session_raises_error(
        self,
        require_podman: None,
    ) -> None:
        """Deleting a nonexistent session raises SessionNotFoundError."""
        backend = PodmanBackend()

        with pytest.raises(SessionNotFoundError):
            backend.delete_session("nonexistent-session-xyz", confirm=True)

    def test_list_sessions_returns_created_sessions(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """List sessions includes created sessions."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            sessions = backend.list_sessions()
            session_names = [s.name for s in sessions]

            assert unique_session_name in session_names

        finally:
            cleanup_session(backend, unique_session_name)

    def test_get_session_returns_session_info(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Get session returns correct session information."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            session = backend.get_session(unique_session_name)

            assert session is not None
            assert session.name == unique_session_name
            assert session.status == "stopped"
            assert session.backend_type == "podman"

        finally:
            cleanup_session(backend, unique_session_name)

    def test_get_nonexistent_session_returns_none(
        self,
        require_podman: None,
    ) -> None:
        """Get session returns None for nonexistent session."""
        backend = PodmanBackend()

        session = backend.get_session("nonexistent-session-xyz")
        assert session is None


class TestPodmanContainerOperations:
    """Test container start/stop operations with real Podman."""

    def test_start_and_stop_session(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Start and stop a session."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            # Start the container (without attaching - just start it)
            container_name = f"paude-{unique_session_name}"
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Verify it's running
            result = subprocess.run(
                ["podman", "inspect", container_name, "--format", "{{.State.Running}}"],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "true", "Container should be running"

            # Stop the session
            backend.stop_session(unique_session_name)

            # Verify it's stopped
            result = subprocess.run(
                ["podman", "inspect", container_name, "--format", "{{.State.Running}}"],
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == "false", "Container should be stopped"

        finally:
            cleanup_session(backend, unique_session_name)


class TestPodmanVolumes:
    """Test volume persistence with real Podman."""

    def test_volume_persists_data(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Data written to the volume persists across container restarts."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"

            # Start container
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Write a test file to the volume
            test_content = "integration-test-data"
            subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    f"echo '{test_content}' > /pvc/test-file.txt",
                ],
                capture_output=True,
                check=True,
            )

            # Stop container
            backend.stop_session(unique_session_name)

            # Start container again
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Verify the file still exists
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "cat",
                    "/pvc/test-file.txt",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert test_content in result.stdout

        finally:
            cleanup_session(backend, unique_session_name)

    def test_workspace_directory_exists(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """The /pvc/workspace directory exists in the container."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"

            # Start container
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Check that /pvc directory exists and is writable
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "test",
                    "-d",
                    "/pvc",
                ],
                capture_output=True,
            )
            assert result.returncode == 0, "/pvc should exist"

            # Check that we can write to /pvc/workspace
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "bash",
                    "-c",
                    "mkdir -p /pvc/workspace && touch /pvc/workspace/test",
                ],
                capture_output=True,
            )
            assert result.returncode == 0, "Should be able to write to /pvc/workspace"

        finally:
            cleanup_session(backend, unique_session_name)


class TestPodmanEnvironment:
    """Test environment variable handling with real Podman."""

    def test_paude_workspace_env_is_set(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """PAUDE_WORKSPACE environment variable is set in container."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"

            # Start container
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Check PAUDE_WORKSPACE is set
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "printenv",
                    "PAUDE_WORKSPACE",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "/pvc/workspace" in result.stdout

        finally:
            cleanup_session(backend, unique_session_name)

    def test_yolo_mode_sets_claude_args(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """YOLO mode sets PAUDE_CLAUDE_ARGS with skip permissions flag."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
                yolo=True,
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"

            # Start container
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            # Check PAUDE_CLAUDE_ARGS contains the skip permissions flag
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "printenv",
                    "PAUDE_CLAUDE_ARGS",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "--dangerously-skip-permissions" in result.stdout

        finally:
            cleanup_session(backend, unique_session_name)


def _start_proxy_session(
    backend: PodmanBackend,
    session_name: str,
    workspace: Path,
    main_image: str,
    proxy_image: str,
    allowed_domains: list[str],
) -> str:
    """Create and start a session with proxy egress filtering.

    Uses PodmanBackend.create_session() which creates:
    - Internal network (paude-net-{session_name})
    - Proxy container on internal + podman networks
    - Main container on internal network only with HTTP_PROXY set

    Then starts both proxy and main containers.

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

    # Start proxy container first (created stopped by create_session)
    proxy_name = f"paude-proxy-{session_name}"
    subprocess.run(
        ["podman", "start", proxy_name],
        capture_output=True,
        check=True,
    )
    # Give squid time to initialize
    time.sleep(2)

    # Start main container
    container_name = f"paude-{session_name}"
    subprocess.run(
        ["podman", "start", container_name],
        capture_output=True,
        check=True,
    )

    # Get proxy IP on the internal network (avoids DNS resolution issues in CI)
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


class TestPodmanProxyEgressFiltering:
    """Test proxy-based egress filtering with real Podman."""

    def test_create_session_with_domains_creates_proxy_and_network(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Creating a session with allowed_domains creates proxy and network."""
        backend = PodmanBackend()
        network_name = f"paude-net-{unique_session_name}"
        proxy_name = f"paude-proxy-{unique_session_name}"
        container_name = f"paude-{unique_session_name}"

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
                allowed_domains=[".googleapis.com"],
                proxy_image=podman_proxy_image,
            )
            backend.create_session(config)

            # Verify proxy container exists
            result = subprocess.run(
                ["podman", "container", "exists", proxy_name],
                capture_output=True,
            )
            assert result.returncode == 0, "Proxy container should exist"

            # Verify network exists
            result = subprocess.run(
                ["podman", "network", "exists", network_name],
                capture_output=True,
            )
            assert result.returncode == 0, "Internal network should exist"

            # Verify main container has HTTP_PROXY env var set
            result = subprocess.run(
                [
                    "podman",
                    "inspect",
                    container_name,
                    "--format",
                    "{{range .Config.Env}}{{println .}}{{end}}",
                ],
                capture_output=True,
                text=True,
            )
            assert f"HTTP_PROXY=http://{proxy_name}:3128" in result.stdout, (
                "Main container should have HTTP_PROXY pointing to proxy"
            )

        finally:
            cleanup_session(backend, unique_session_name)

    def test_proxy_allows_permitted_domains(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Proxy allows requests to permitted domains."""
        backend = PodmanBackend()
        container_name = f"paude-{unique_session_name}"

        try:
            proxy_ip = _start_proxy_session(
                backend=backend,
                session_name=unique_session_name,
                workspace=temp_workspace,
                main_image=podman_test_image,
                proxy_image=podman_proxy_image,
                allowed_domains=[".googleapis.com"],
            )

            # Curl an allowed domain through the proxy using explicit IP.
            # Uses -x to specify proxy directly, bypassing DNS resolution
            # (aardvark-dns may not work on --internal networks in CI).
            proxy_name = f"paude-proxy-{unique_session_name}"
            subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-x",
                    f"http://{proxy_ip}:3128",
                    "--connect-timeout",
                    "10",
                    "-m",
                    "15",
                    "https://oauth2.googleapis.com/",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Check proxy logs: squid only logs BLOCKED requests
            # (access_log ... !allowed_domains). If the domain appears in
            # the logs, the proxy denied it. Absence means it was allowed,
            # regardless of whether the upstream was actually reachable.
            log_result = subprocess.run(
                ["podman", "logs", proxy_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert "oauth2.googleapis.com" not in log_result.stdout, (
                f"Proxy blocked an allowed domain, proxy_logs={log_result.stdout}"
            )

        finally:
            cleanup_session(backend, unique_session_name)

    def test_proxy_blocks_non_permitted_domains(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Proxy blocks requests to non-permitted domains with 403."""
        backend = PodmanBackend()
        container_name = f"paude-{unique_session_name}"

        try:
            proxy_ip = _start_proxy_session(
                backend=backend,
                session_name=unique_session_name,
                workspace=temp_workspace,
                main_image=podman_test_image,
                proxy_image=podman_proxy_image,
                allowed_domains=[".googleapis.com"],
            )

            # Curl a blocked domain through the proxy using explicit IP
            # (bypasses DNS resolution issues in CI)
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "curl",
                    "-s",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "-x",
                    f"http://{proxy_ip}:3128",
                    "--connect-timeout",
                    "10",
                    "-m",
                    "15",
                    "https://example.com/",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Squid may return 403 (explicit denial) or reset the connection
            # (curl exit code != 0, http_code 000) depending on version.
            # Both mean the request was blocked.
            http_code = result.stdout.strip()
            assert http_code == "403" or (
                http_code == "000" and result.returncode != 0
            ), (
                f"curl to blocked domain should be denied (403 or connection reset), "
                f"got http_code={http_code}, returncode={result.returncode}, "
                f"stderr={result.stderr}"
            )

        finally:
            cleanup_session(backend, unique_session_name)

    def test_no_direct_internet_without_proxy(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Main container cannot reach the internet bypassing the proxy."""
        backend = PodmanBackend()
        container_name = f"paude-{unique_session_name}"

        try:
            _start_proxy_session(
                backend=backend,
                session_name=unique_session_name,
                workspace=temp_workspace,
                main_image=podman_test_image,
                proxy_image=podman_proxy_image,
                allowed_domains=[".googleapis.com"],
            )

            # Try to reach the internet directly, bypassing proxy
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container_name,
                    "curl",
                    "--noproxy",
                    "*",
                    "-sf",
                    "--connect-timeout",
                    "5",
                    "-m",
                    "10",
                    "https://example.com/",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Should fail — internal network has no gateway
            assert result.returncode != 0, (
                "Direct internet access should fail on internal network"
            )

        finally:
            cleanup_session(backend, unique_session_name)

    def test_delete_session_cleans_up_proxy_and_network(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Deleting a proxy session removes proxy container and network."""
        network_name = f"paude-net-{unique_session_name}"
        proxy_name = f"paude-proxy-{unique_session_name}"

        backend = PodmanBackend()
        config = SessionConfig(
            name=unique_session_name,
            workspace=temp_workspace,
            image=podman_test_image,
            allowed_domains=[".googleapis.com"],
            proxy_image=podman_proxy_image,
        )
        backend.create_session(config)

        # Delete the session
        backend.delete_session(unique_session_name, confirm=True)

        # Verify proxy container is gone
        result = subprocess.run(
            ["podman", "container", "exists", proxy_name],
            capture_output=True,
        )
        assert result.returncode != 0, "Proxy container should be deleted"

        # Verify network is gone
        result = subprocess.run(
            ["podman", "network", "exists", network_name],
            capture_output=True,
        )
        assert result.returncode != 0, "Network should be deleted"
