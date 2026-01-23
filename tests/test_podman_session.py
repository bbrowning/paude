"""Tests for Podman backend session management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import SessionConfig
from paude.backends.podman import (
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
    _decode_path,
    _encode_path,
    _generate_session_name,
)


class TestHelperFunctions:
    """Tests for session helper functions."""

    def test_generate_session_name_includes_project_name(self) -> None:
        """Session name includes sanitized project name."""
        workspace = Path("/home/user/my-project")
        name = _generate_session_name(workspace)
        assert name.startswith("my-project-")

    def test_generate_session_name_sanitizes_special_chars(self) -> None:
        """Session name sanitizes special characters."""
        workspace = Path("/home/user/Project With Spaces!")
        name = _generate_session_name(workspace)
        # Spaces and ! become hyphens, lowercase
        assert "project-with-spaces" in name

    def test_generate_session_name_truncates_long_names(self) -> None:
        """Session name truncates long project names."""
        workspace = Path("/home/user/this-is-a-very-long-project-name-indeed")
        name = _generate_session_name(workspace)
        # Should be truncated to 20 chars + suffix
        parts = name.rsplit("-", 1)
        assert len(parts[0]) <= 20

    def test_generate_session_name_has_unique_suffix(self) -> None:
        """Session names have unique suffixes."""
        workspace = Path("/home/user/project")
        names = [_generate_session_name(workspace) for _ in range(10)]
        # All names should be unique
        assert len(set(names)) == 10

    def test_encode_decode_path_roundtrip(self) -> None:
        """Path encoding and decoding is reversible."""
        original = Path("/home/user/my project/src")
        encoded = _encode_path(original)
        decoded = _decode_path(encoded)
        assert decoded == original

    def test_encode_path_is_url_safe(self) -> None:
        """Encoded paths are URL-safe (no special chars)."""
        path = Path("/home/user/project with spaces & symbols!")
        encoded = _encode_path(path)
        # URL-safe base64 only uses alphanumeric, -, _, =
        assert all(c.isalnum() or c in "-_=" for c in encoded)

    def test_decode_path_handles_invalid_input(self) -> None:
        """Decoding invalid input returns the raw input as path."""
        invalid = "not-valid-base64!!!"
        result = _decode_path(invalid)
        # Should return the raw input as a Path (fallback)
        assert result == Path(invalid)


class TestPodmanBackendCreateSession:
    """Tests for PodmanBackend.create_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_with_explicit_name(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session uses provided name."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="my-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        session = backend.create_session(config)

        assert session.name == "my-session"
        assert session.status == "stopped"
        assert session.workspace == Path("/home/user/project")
        assert session.backend_type == "podman"
        assert session.container_id == "paude-my-session"
        assert session.volume_name == "paude-my-session-workspace"

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_generates_name_when_not_provided(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session generates name from workspace."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name=None,
            workspace=Path("/home/user/my-project"),
            image="paude:latest",
        )
        session = backend.create_session(config)

        assert session.name.startswith("my-project-")
        assert len(session.name) > len("my-project-")

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_creates_volume(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session creates a named volume."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        backend.create_session(config)

        mock_runner.create_volume.assert_called_once()
        call_args = mock_runner.create_volume.call_args
        assert call_args[0][0] == "paude-test-session-workspace"
        assert "labels" in call_args[1]

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_creates_stopped_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session creates container in stopped state."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        session = backend.create_session(config)

        mock_runner.create_container.assert_called_once()
        # start_container should NOT be called
        mock_runner.start_container.assert_not_called()
        assert session.status == "stopped"

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_raises_if_exists(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session raises SessionExistsError if session already exists."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="existing-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        with pytest.raises(SessionExistsError) as excinfo:
            backend.create_session(config)
        assert "existing-session" in str(excinfo.value)

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_cleans_up_on_container_failure(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session cleans up volume if container creation fails."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner.create_container.side_effect = RuntimeError("Container failed")
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        with pytest.raises(RuntimeError):
            backend.create_session(config)

        # Volume should be cleaned up
        mock_runner.remove_volume.assert_called_once()

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_with_yolo_mode(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Create session with yolo=True adds permission skip flag."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        config = SessionConfig(
            name="yolo-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            yolo=True,
        )
        backend.create_session(config)

        # Check that PAUDE_CLAUDE_ARGS env includes the skip flag
        call_args = mock_runner.create_container.call_args
        env = call_args[1]["env"]
        assert "--dangerously-skip-permissions" in env.get("PAUDE_CLAUDE_ARGS", "")


class TestPodmanBackendDeleteSession:
    """Tests for PodmanBackend.delete_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_requires_confirmation(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session requires confirm=True."""
        backend = PodmanBackend()

        with pytest.raises(ValueError, match="(?i)confirmation"):
            backend.delete_session("my-session", confirm=False)

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_removes_container_and_volume(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session removes both container and volume."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.delete_session("my-session", confirm=True)

        mock_runner.remove_container.assert_called_once_with(
            "paude-my-session", force=True
        )
        mock_runner.remove_volume.assert_called_once_with(
            "paude-my-session-workspace", force=True
        )

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_stops_running_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session stops container if running."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.delete_session("running-session", confirm=True)

        mock_runner.stop_container_graceful.assert_called_once()

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_raises_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session raises SessionNotFoundError if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner.volume_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        with pytest.raises(SessionNotFoundError) as excinfo:
            backend.delete_session("nonexistent", confirm=True)
        assert "nonexistent" in str(excinfo.value)

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_cleans_orphaned_volume(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session cleans up orphaned volume without container."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner.volume_exists.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.delete_session("orphaned", confirm=True)

        mock_runner.remove_container.assert_not_called()
        mock_runner.remove_volume.assert_called_once()


class TestPodmanBackendStartSession:
    """Tests for PodmanBackend.start_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_starts_stopped_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Start session starts a stopped container."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.start_session("my-session")

        mock_runner.start_container.assert_called_once_with("paude-my-session")
        mock_runner.attach_container.assert_called_once()
        assert exit_code == 0

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_connects_if_already_running(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Start session connects if container already running."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.get_container_state.return_value = "running"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.start_session("running-session")

        # Should NOT call start_container since already running
        mock_runner.start_container.assert_not_called()
        mock_runner.attach_container.assert_called_once()
        assert exit_code == 0

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_raises_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Start session raises SessionNotFoundError if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        with pytest.raises(SessionNotFoundError) as excinfo:
            backend.start_session("nonexistent")
        assert "nonexistent" in str(excinfo.value)


class TestPodmanBackendStopSession:
    """Tests for PodmanBackend.stop_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_stops_running_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Stop session stops a running container."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.stop_session("my-session")

        mock_runner.stop_container_graceful.assert_called_once_with("paude-my-session")

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_noop_if_already_stopped(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Stop session is no-op if container already stopped."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.stop_session("stopped-session")

        mock_runner.stop_container_graceful.assert_not_called()

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_noop_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Stop session is no-op if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        # Should not raise, just print and return
        backend.stop_session("nonexistent")

        mock_runner.stop_container_graceful.assert_not_called()


class TestPodmanBackendConnectSession:
    """Tests for PodmanBackend.connect_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_connect_session_attaches_to_running_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Connect session attaches to a running container."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.connect_session("my-session")

        mock_runner.attach_container.assert_called_once()
        assert exit_code == 0

    @patch("paude.backends.podman.ContainerRunner")
    def test_connect_session_fails_if_not_running(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Connect session returns error if container not running."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.connect_session("stopped-session")

        mock_runner.attach_container.assert_not_called()
        assert exit_code == 1

    @patch("paude.backends.podman.ContainerRunner")
    def test_connect_session_fails_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Connect session returns error if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.connect_session("nonexistent")

        assert exit_code == 1


class TestPodmanBackendListSessions:
    """Tests for PodmanBackend.list_sessions."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_returns_paude_containers(
        self, mock_runner_class: MagicMock
    ) -> None:
        """List sessions returns containers with paude labels."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-test-session"],
                "State": "running",
                "Labels": {
                    "app": "paude",
                    "paude.io/session-name": "test-session",
                    "paude.io/workspace": _encode_path(Path("/home/user/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].name == "test-session"
        assert sessions[0].status == "running"
        assert sessions[0].workspace == Path("/home/user/project")

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_maps_container_states(
        self, mock_runner_class: MagicMock
    ) -> None:
        """List sessions maps container states to session statuses."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-session1"],
                "State": "exited",
                "Labels": {
                    "paude.io/session-name": "session1",
                    "paude.io/workspace": _encode_path(Path("/path1")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            },
            {
                "Names": ["paude-session2"],
                "State": "dead",
                "Labels": {
                    "paude.io/session-name": "session2",
                    "paude.io/workspace": _encode_path(Path("/path2")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            },
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        sessions = backend.list_sessions()

        assert len(sessions) == 2
        assert sessions[0].status == "stopped"  # exited -> stopped
        assert sessions[1].status == "error"  # dead -> error

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_returns_empty_when_no_containers(
        self, mock_runner_class: MagicMock
    ) -> None:
        """List sessions returns empty list when no paude containers."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = []
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        sessions = backend.list_sessions()

        assert sessions == []


class TestPodmanBackendGetSession:
    """Tests for PodmanBackend.get_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_get_session_returns_session_if_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Get session returns Session object if found."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": _encode_path(Path("/home/user/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.get_session("my-session")

        assert session is not None
        assert session.name == "my-session"
        assert session.status == "running"

    @patch("paude.backends.podman.ContainerRunner")
    def test_get_session_returns_none_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Get session returns None if session not found."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = []
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.get_session("nonexistent")

        assert session is None


class TestPodmanBackendSyncSession:
    """Tests for PodmanBackend.sync_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_sync_session_is_noop_for_podman(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Sync session is a no-op for Podman (volumes are live-mounted)."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        # Should not raise, just print message
        backend.sync_session("my-session", direction="both")

        # No rsync or copy operations should be performed
        # (Podman uses direct volume mounts)


class TestPodmanBackendFindSessionForWorkspace:
    """Tests for PodmanBackend.find_session_for_workspace."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_find_session_returns_matching_session(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Find session returns session for matching workspace."""
        workspace = Path("/home/user/my-project")
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-project-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "project-session",
                    "paude.io/workspace": _encode_path(workspace),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.find_session_for_workspace(workspace)

        assert session is not None
        assert session.name == "project-session"
        assert session.workspace == workspace

    @patch("paude.backends.podman.ContainerRunner")
    def test_find_session_returns_none_when_no_match(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Find session returns None when no matching workspace."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-other-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "other-session",
                    "paude.io/workspace": _encode_path(Path("/other/path")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.find_session_for_workspace(Path("/home/user/my-project"))

        assert session is None
