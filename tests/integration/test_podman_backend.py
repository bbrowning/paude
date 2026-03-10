"""Integration tests for Podman backend with real Podman operations."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from paude.backends.base import Session, SessionConfig
from paude.backends.podman import (
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
)

from .conftest import _start_proxy_session, cleanup_session

pytestmark = [pytest.mark.integration, pytest.mark.podman]


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

    def test_delete_session_cleans_orphaned_volume(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Deleting a session whose container was removed still cleans the volume."""
        backend = PodmanBackend()
        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
            )
            backend.create_session(config)

            volume_name = f"paude-{unique_session_name}-workspace"

            # Manually remove the container, leaving the volume orphaned
            subprocess.run(
                ["podman", "rm", "-f", f"paude-{unique_session_name}"],
                capture_output=True,
                check=True,
            )

            # Verify the volume still exists
            result = subprocess.run(
                ["podman", "volume", "exists", volume_name],
                capture_output=True,
            )
            assert result.returncode == 0, "Volume should still exist"

            # delete_session should clean up the orphaned volume
            backend.delete_session(unique_session_name, confirm=True)

            # Verify the volume is now gone
            result = subprocess.run(
                ["podman", "volume", "exists", volume_name],
                capture_output=True,
            )
            assert result.returncode != 0, "Volume should be deleted"
        finally:
            cleanup_session(backend, unique_session_name)


class TestPodmanStoppedSession:
    """Tests against a shared stopped session (class-scoped)."""

    def test_list_sessions_returns_created_sessions(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """List sessions includes created sessions."""
        backend, name, _session, _workspace = stopped_session

        sessions = backend.list_sessions()
        session_names = [s.name for s in sessions]

        assert name in session_names

    def test_get_session_returns_session_info(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """Get session returns correct session information."""
        backend, name, _session, _workspace = stopped_session

        session = backend.get_session(name)

        assert session is not None
        assert session.name == name
        assert session.status == "stopped"
        assert session.backend_type == "podman"

    def test_get_nonexistent_session_returns_none(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """Get session returns None for nonexistent session."""
        backend, _name, _session, _workspace = stopped_session

        session = backend.get_session("nonexistent-session-xyz")
        assert session is None

    def test_find_session_for_workspace(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """find_session_for_workspace returns the matching session or None."""
        backend, name, _session, workspace = stopped_session

        # Should find the session for the workspace
        session = backend.find_session_for_workspace(workspace)
        assert session is not None
        assert session.name == name

        # Should return None for a non-existent workspace
        result = backend.find_session_for_workspace(
            Path("/tmp/nonexistent-workspace-xyz")
        )
        assert result is None

    def test_stop_already_stopped_session(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """Stopping an already-stopped session should not raise."""
        backend, name, _session, _workspace = stopped_session
        # Session is created in stopped state; stopping again should be fine
        backend.stop_session(name)

    def test_stop_nonexistent_session(
        self, stopped_session: tuple[PodmanBackend, str, Session, Path]
    ) -> None:
        """Stopping a nonexistent session should not raise."""
        backend, _name, _session, _workspace = stopped_session
        # Should print "not found" to stderr but not raise
        backend.stop_session("nonexistent-session-xyz")


class TestPodmanRunningSession:
    """Tests against a shared running session (class-scoped)."""

    def test_workspace_directory_exists(
        self, running_session: tuple[PodmanBackend, str, Path]
    ) -> None:
        """The /pvc/workspace directory exists in the container."""
        _backend, name, _workspace = running_session
        container_name = f"paude-{name}"

        # Check that /pvc directory exists
        result = subprocess.run(
            ["podman", "exec", container_name, "test", "-d", "/pvc"],
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

    def test_paude_workspace_env_is_set(
        self, running_session: tuple[PodmanBackend, str, Path]
    ) -> None:
        """PAUDE_WORKSPACE environment variable is set in container."""
        _backend, name, _workspace = running_session
        container_name = f"paude-{name}"

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

    def test_exec_in_session(
        self, running_session: tuple[PodmanBackend, str, Path]
    ) -> None:
        """Exec a command in a running session and check output."""
        backend, name, _workspace = running_session

        returncode, stdout, _stderr = backend.exec_in_session(name, "echo hello")

        assert returncode == 0
        assert "hello" in stdout

    def test_copy_to_and_from_session(
        self,
        running_session: tuple[PodmanBackend, str, Path],
        tmp_path: Path,
    ) -> None:
        """Copy a file into a session and back out, verifying contents."""
        backend, name, _workspace = running_session

        # Write a local file with known content
        local_file = tmp_path / "upload.txt"
        content = "integration-test-copy-data"
        local_file.write_text(content)

        # Copy into the session
        backend.copy_to_session(
            name,
            str(local_file),
            "/pvc/workspace/copied.txt",
        )

        # Verify the file arrived
        returncode, stdout, _stderr = backend.exec_in_session(
            name, "cat /pvc/workspace/copied.txt"
        )
        assert returncode == 0
        assert content in stdout

        # Copy back out
        download_path = tmp_path / "download.txt"
        backend.copy_from_session(
            name,
            "/pvc/workspace/copied.txt",
            str(download_path),
        )
        assert download_path.read_text().strip() == content


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


class TestPodmanYoloMode:
    """Test YOLO mode with real Podman."""

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


def _get_container_env(container_name: str, var_name: str) -> str | None:
    """Get an environment variable from a running container.

    Returns the value if set, None if the variable is not set.
    """
    result = subprocess.run(
        ["podman", "exec", container_name, "printenv", var_name],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


class TestPodmanGeminiAgent:
    """Test Gemini agent sessions with real Podman."""

    def test_gemini_session_stores_agent_label(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Creating a session with agent='gemini' stores the agent label."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
                agent="gemini",
            )
            session = backend.create_session(config)

            assert session.agent == "gemini"

            # Verify agent persists through get_session
            retrieved = backend.get_session(unique_session_name)
            assert retrieved is not None
            assert retrieved.agent == "gemini"

        finally:
            cleanup_session(backend, unique_session_name)

    def test_gemini_yolo_mode_sets_agent_args(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """YOLO mode with Gemini sets PAUDE_AGENT_ARGS with --yolo flag."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
                yolo=True,
                agent="gemini",
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            agent_args = _get_container_env(container_name, "PAUDE_AGENT_ARGS")
            assert agent_args is not None, "PAUDE_AGENT_ARGS should be set"
            assert "--yolo" in agent_args

            # Gemini should NOT set PAUDE_CLAUDE_ARGS
            assert _get_container_env(container_name, "PAUDE_CLAUDE_ARGS") is None, (
                "PAUDE_CLAUDE_ARGS should not be set for Gemini agent"
            )

        finally:
            cleanup_session(backend, unique_session_name)

    def test_gemini_agent_env_vars_set(
        self,
        require_podman: None,
        require_test_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
    ) -> None:
        """Gemini session has correct agent seed env vars."""
        backend = PodmanBackend()

        try:
            config = SessionConfig(
                name=unique_session_name,
                workspace=temp_workspace,
                image=podman_test_image,
                agent="gemini",
            )
            backend.create_session(config)

            container_name = f"paude-{unique_session_name}"
            subprocess.run(
                ["podman", "start", container_name],
                capture_output=True,
                check=True,
            )

            assert _get_container_env(container_name, "PAUDE_AGENT_SEED_DIR") == (
                "/tmp/gemini.seed"
            )
            assert _get_container_env(container_name, "PAUDE_AGENT_NAME") == "gemini"

        finally:
            cleanup_session(backend, unique_session_name)


class TestPodmanProxySetup:
    """Test proxy session creation with real Podman."""

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


class TestPodmanProxyBehavior:
    """Test proxy behavior with a shared running proxy session (class-scoped)."""

    def test_proxy_allows_permitted_domains(
        self, running_proxy_session: tuple[PodmanBackend, str, str]
    ) -> None:
        """Proxy allows requests to permitted domains."""
        backend, name, proxy_ip = running_proxy_session
        container_name = f"paude-{name}"

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
                "3",
                "-m",
                "5",
                "https://oauth2.googleapis.com/",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # Squid only writes to the blocked log for denied requests.
        # If the domain appears there, the proxy blocked it.
        blocked_log = backend.get_proxy_blocked_log(name)
        assert blocked_log is not None
        assert "oauth2.googleapis.com" not in blocked_log, (
            f"Proxy blocked an allowed domain, blocked_log={blocked_log}"
        )

    def test_proxy_blocks_non_permitted_domains(
        self, running_proxy_session: tuple[PodmanBackend, str, str]
    ) -> None:
        """Proxy blocks requests to non-permitted domains with 403."""
        _backend, name, proxy_ip = running_proxy_session
        container_name = f"paude-{name}"

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
                "3",
                "-m",
                "5",
                "https://example.com/",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        http_code = result.stdout.strip()
        assert http_code == "403" or (http_code == "000" and result.returncode != 0), (
            f"curl to blocked domain should be denied (403 or connection reset), "
            f"got http_code={http_code}, returncode={result.returncode}, "
            f"stderr={result.stderr}"
        )

    def test_no_direct_internet_without_proxy(
        self, running_proxy_session: tuple[PodmanBackend, str, str]
    ) -> None:
        """Main container cannot reach the internet bypassing the proxy."""
        _backend, name, _proxy_ip = running_proxy_session
        container_name = f"paude-{name}"

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
                "3",
                "-m",
                "5",
                "https://example.com/",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0, (
            "Direct internet access should fail on internal network"
        )

    def test_get_allowed_domains(
        self, running_proxy_session: tuple[PodmanBackend, str, str]
    ) -> None:
        """get_allowed_domains returns the configured domain list."""
        backend, name, _proxy_ip = running_proxy_session

        domains = backend.get_allowed_domains(name)
        assert domains == [".googleapis.com"]

    def test_get_proxy_blocked_log(
        self, running_proxy_session: tuple[PodmanBackend, str, str]
    ) -> None:
        """get_proxy_blocked_log reflects blocked requests."""
        backend, name, proxy_ip = running_proxy_session
        container_name = f"paude-{name}"

        # Initial log should not contain our unique test domain
        initial_log = backend.get_proxy_blocked_log(name)
        assert initial_log is not None

        # Make a request to a blocked domain through the proxy
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
                "3",
                "-m",
                "5",
                "https://blocked-test.example.com/",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        # Blocked log should now contain the blocked domain
        updated_log = backend.get_proxy_blocked_log(name)
        assert updated_log is not None
        assert "blocked-test.example.com" in updated_log


class TestPodmanProxyDomainUpdate:
    """Tests that modify proxy configuration (need their own session)."""

    def test_update_allowed_domains(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """update_allowed_domains changes the proxy's domain list."""
        backend = PodmanBackend()
        try:
            _start_proxy_session(
                backend=backend,
                session_name=unique_session_name,
                workspace=temp_workspace,
                main_image=podman_test_image,
                proxy_image=podman_proxy_image,
                allowed_domains=[".googleapis.com"],
            )

            # Update domains to also allow example.com
            backend.update_allowed_domains(
                unique_session_name, [".example.com", ".googleapis.com"]
            )

            # Verify updated domains
            domains = backend.get_allowed_domains(unique_session_name)
            assert domains is not None
            assert ".example.com" in domains
            assert ".googleapis.com" in domains

            # Need to get new proxy IP after recreation
            proxy_name = f"paude-proxy-{unique_session_name}"
            network_name = f"paude-net-{unique_session_name}"
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
            new_proxy_ip = result.stdout.strip()

            # Verify example.com is now allowed
            container_name = f"paude-{unique_session_name}"
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
                    f"http://{new_proxy_ip}:3128",
                    "--connect-timeout",
                    "3",
                    "-m",
                    "5",
                    "https://example.com/",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            # Check blocked log — example.com should NOT appear (it's allowed now)
            blocked_log = backend.get_proxy_blocked_log(unique_session_name)
            assert blocked_log is not None
            assert "example.com" not in blocked_log
        finally:
            cleanup_session(backend, unique_session_name)
