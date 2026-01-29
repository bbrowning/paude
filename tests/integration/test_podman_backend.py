"""Integration tests for Podman backend with real Podman operations."""

from __future__ import annotations

import subprocess
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
                ["podman", "volume", "exists", f"paude-{unique_session_name}-workspace"],
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
                    "podman", "exec", container_name,
                    "bash", "-c", f"echo '{test_content}' > /pvc/test-file.txt",
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
                    "podman", "exec", container_name,
                    "cat", "/pvc/test-file.txt",
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
                    "podman", "exec", container_name,
                    "test", "-d", "/pvc",
                ],
                capture_output=True,
            )
            assert result.returncode == 0, "/pvc should exist"

            # Check that we can write to /pvc/workspace
            result = subprocess.run(
                [
                    "podman", "exec", container_name,
                    "bash", "-c", "mkdir -p /pvc/workspace && touch /pvc/workspace/test",
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
                    "podman", "exec", container_name,
                    "printenv", "PAUDE_WORKSPACE",
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
                    "podman", "exec", container_name,
                    "printenv", "PAUDE_CLAUDE_ARGS",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "--dangerously-skip-permissions" in result.stdout

        finally:
            cleanup_session(backend, unique_session_name)
