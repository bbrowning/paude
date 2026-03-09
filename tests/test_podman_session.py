"""Tests for Podman backend session management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import SessionConfig
from paude.backends.podman import (
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_PROXY_IMAGE,
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
    _generate_session_name,
)
from paude.backends.shared import decode_path as _decode_path_raw
from paude.backends.shared import encode_path as _encode_path_raw
from paude.container.runner import (
    PAUDE_LABEL_APP,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
)


def encode_path(path: Path) -> str:
    """Encode path with url_safe=True (matches Podman backend usage)."""
    return _encode_path_raw(path, url_safe=True)


def decode_path(encoded: str) -> Path:
    """Decode path with url_safe=True (matches Podman backend usage)."""
    return _decode_path_raw(encoded, url_safe=True)


def _make_backend(
    mock_runner: MagicMock | None = None,
    mock_network_manager: MagicMock | None = None,
    mock_volume_manager: MagicMock | None = None,
) -> PodmanBackend:
    """Create a PodmanBackend with mocked runner, network, and volume manager."""
    backend = PodmanBackend()
    if mock_runner is not None:
        backend._runner = mock_runner
    if mock_network_manager is not None:
        backend._network_manager = mock_network_manager
    # Always mock volume manager to prevent real podman calls
    backend._volume_manager = mock_volume_manager or MagicMock()
    return backend


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
        encoded = encode_path(original)
        decoded = decode_path(encoded)
        assert decoded == original

    def test_encode_path_is_url_safe(self) -> None:
        """Encoded paths are URL-safe (no special chars)."""
        path = Path("/home/user/project with spaces & symbols!")
        encoded = encode_path(path)
        # URL-safe base64 only uses alphanumeric, -, _, =
        assert all(c.isalnum() or c in "-_=" for c in encoded)

    def test_decode_path_handles_invalid_input(self) -> None:
        """Decoding invalid input returns the raw input as path."""
        invalid = "not-valid-base64!!!"
        result = decode_path(invalid)
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
        backend._volume_manager = MagicMock()

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
        backend._volume_manager = MagicMock()

        config = SessionConfig(
            name=None,
            workspace=Path("/home/user/my-project"),
            image="paude:latest",
        )
        session = backend.create_session(config)

        assert session.name.startswith("my-project-")
        assert len(session.name) > len("my-project-")

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_creates_volume(self, mock_runner_class: MagicMock) -> None:
        """Create session creates a named volume."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_volume = MagicMock()

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = mock_volume

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        backend.create_session(config)

        mock_volume.create_volume.assert_called_once()
        call_args = mock_volume.create_volume.call_args
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
        backend._volume_manager = MagicMock()

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
        backend._volume_manager = MagicMock()

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
        mock_volume = MagicMock()

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = mock_volume

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        with pytest.raises(RuntimeError):
            backend.create_session(config)

        # Volume should be cleaned up
        mock_volume.remove_volume.assert_called_once()

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_with_yolo_mode(self, mock_runner_class: MagicMock) -> None:
        """Create session with yolo=True adds permission skip flag."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = MagicMock()

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
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_volume = MagicMock()

        backend = _make_backend(mock_runner, MagicMock(), mock_volume)

        backend.delete_session("my-session", confirm=True)

        mock_runner.remove_container.assert_called_once_with(
            "paude-my-session", force=True
        )
        mock_volume.remove_volume.assert_called_once_with(
            "paude-my-session-workspace", force=True
        )

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_stops_running_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session stops container if running."""
        mock_runner = MagicMock()
        # Main container exists, proxy does not
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-running-session"
        )
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner, MagicMock())

        backend.delete_session("running-session", confirm=True)

        mock_runner.stop_container_graceful.assert_called_once()

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_raises_if_not_found(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Delete session raises SessionNotFoundError if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_volume = MagicMock()
        mock_volume.volume_exists.return_value = False

        backend = _make_backend(mock_runner, MagicMock(), mock_volume)

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
        mock_runner_class.return_value = mock_runner
        mock_volume = MagicMock()
        mock_volume.volume_exists.return_value = True

        backend = _make_backend(mock_runner, MagicMock(), mock_volume)

        backend.delete_session("orphaned", confirm=True)

        mock_runner.remove_container.assert_not_called()
        mock_volume.remove_volume.assert_called_once()


class TestPodmanBackendStartSession:
    """Tests for PodmanBackend.start_session."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_starts_stopped_container(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Start session starts a stopped container."""
        mock_runner = MagicMock()
        # Main container exists, no proxy
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

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
        # Main container exists, no proxy
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-running-session"
        )
        mock_runner.container_running.return_value = True
        mock_runner.get_container_state.return_value = "running"
        mock_runner.attach_container.return_value = 0
        # Workspace has .git
        mock_runner.exec_in_container.return_value = MagicMock(returncode=0)
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

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
        # Main container exists, no proxy
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

        backend.stop_session("my-session")

        mock_runner.stop_container_graceful.assert_called_once_with("paude-my-session")

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_noop_if_already_stopped(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Stop session is no-op if container already stopped."""
        mock_runner = MagicMock()
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-stopped-session"
        )
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

        backend.stop_session("stopped-session")

        mock_runner.stop_container_graceful.assert_not_called()

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_noop_if_not_found(self, mock_runner_class: MagicMock) -> None:
        """Stop session is no-op if session doesn't exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

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
        # Workspace has .git directory
        mock_runner.exec_in_container.return_value = MagicMock(returncode=0)
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.connect_session("my-session")

        mock_runner.attach_container.assert_called_once()
        assert exit_code == 0

    @patch("paude.backends.podman.ContainerRunner")
    def test_connect_session_shows_empty_workspace_message(
        self, mock_runner_class: MagicMock, capsys
    ) -> None:
        """Connect session shows message when workspace is empty."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner.attach_container.return_value = 0
        # Workspace is empty (no .git directory)
        mock_runner.exec_in_container.return_value = MagicMock(returncode=1)
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        exit_code = backend.connect_session("my-session")

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Workspace is empty" in captured.err
        assert "paude remote add my-session" in captured.err
        assert "git push paude-my-session main" in captured.err

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
                    "paude.io/workspace": encode_path(Path("/home/user/project")),
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
                    "paude.io/workspace": encode_path(Path("/path1")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            },
            {
                "Names": ["paude-session2"],
                "State": "dead",
                "Labels": {
                    "paude.io/session-name": "session2",
                    "paude.io/workspace": encode_path(Path("/path2")),
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
                    "paude.io/workspace": encode_path(Path("/home/user/project")),
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
                    "paude.io/workspace": encode_path(workspace),
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
                    "paude.io/workspace": encode_path(Path("/other/path")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.find_session_for_workspace(Path("/home/user/my-project"))

        assert session is None


class TestPodmanBackendGcpAdcSecret:
    """Tests for GCP ADC secret handling."""

    @patch("paude.backends.podman.Path")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_creates_secret_when_adc_exists(
        self, mock_runner_class: MagicMock, mock_path_class: MagicMock
    ) -> None:
        """create_session creates a Podman secret when ADC file exists."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        # Mock Path.home() to return a path where ADC exists
        mock_home = MagicMock()
        mock_adc = MagicMock()
        mock_adc.is_file.return_value = True
        mock_home.__truediv__ = lambda self, key: (
            MagicMock(
                __truediv__=lambda self, k: MagicMock(
                    __truediv__=lambda self, k2: mock_adc
                )
            )
        )
        mock_path_class.home.return_value = mock_home

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = MagicMock()

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        backend.create_session(config)

        mock_runner.create_secret.assert_called_once()
        assert mock_runner.create_secret.call_args[0][0] == "paude-gcp-adc"

    @patch("paude.backends.podman.Path")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_passes_secret_to_create_container(
        self, mock_runner_class: MagicMock, mock_path_class: MagicMock
    ) -> None:
        """create_session passes the secret spec to create_container."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        mock_home = MagicMock()
        mock_adc = MagicMock()
        mock_adc.is_file.return_value = True
        mock_home.__truediv__ = lambda self, key: (
            MagicMock(
                __truediv__=lambda self, k: MagicMock(
                    __truediv__=lambda self, k2: mock_adc
                )
            )
        )
        mock_path_class.home.return_value = mock_home

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = MagicMock()

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        backend.create_session(config)

        call_kwargs = mock_runner.create_container.call_args[1]
        expected_target = (
            "/home/paude/.config/gcloud/application_default_credentials.json"
        )
        assert call_kwargs["secrets"] == [f"paude-gcp-adc,target={expected_target}"]

    @patch("paude.backends.podman.Path")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_skips_secret_when_adc_missing(
        self, mock_runner_class: MagicMock, mock_path_class: MagicMock
    ) -> None:
        """create_session skips secret when ADC file does not exist."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner

        mock_home = MagicMock()
        mock_adc = MagicMock()
        mock_adc.is_file.return_value = False
        mock_home.__truediv__ = lambda self, key: (
            MagicMock(
                __truediv__=lambda self, k: MagicMock(
                    __truediv__=lambda self, k2: mock_adc
                )
            )
        )
        mock_path_class.home.return_value = mock_home

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = MagicMock()

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )
        backend.create_session(config)

        mock_runner.create_secret.assert_not_called()
        call_kwargs = mock_runner.create_container.call_args[1]
        assert call_kwargs["secrets"] is None

    @patch("paude.backends.podman.Path")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_cleans_up_secret_on_failure(
        self, mock_runner_class: MagicMock, mock_path_class: MagicMock
    ) -> None:
        """create_session cleans up the secret when container creation fails."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner.create_container.side_effect = RuntimeError("Container failed")
        mock_runner_class.return_value = mock_runner

        mock_home = MagicMock()
        mock_adc = MagicMock()
        mock_adc.is_file.return_value = True
        mock_home.__truediv__ = lambda self, key: (
            MagicMock(
                __truediv__=lambda self, k: MagicMock(
                    __truediv__=lambda self, k2: mock_adc
                )
            )
        )
        mock_path_class.home.return_value = mock_home

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._volume_manager = MagicMock()

        config = SessionConfig(
            name="test-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
        )

        with pytest.raises(RuntimeError):
            backend.create_session(config)

        # Secret should be cleaned up on failure
        mock_runner.remove_secret.assert_called_once_with("paude-gcp-adc")

    @patch("paude.backends.podman.Path")
    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_recreates_secret(
        self, mock_runner_class: MagicMock, mock_path_class: MagicMock
    ) -> None:
        """start_session recreates the secret before starting."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = True
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        mock_home = MagicMock()
        mock_adc = MagicMock()
        mock_adc.is_file.return_value = True
        mock_home.__truediv__ = lambda self, key: (
            MagicMock(
                __truediv__=lambda self, k: MagicMock(
                    __truediv__=lambda self, k2: mock_adc
                )
            )
        )
        mock_path_class.home.return_value = mock_home

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.start_session("my-session")

        mock_runner.create_secret.assert_called_once()
        assert mock_runner.create_secret.call_args[0][0] == "paude-gcp-adc"


# ---------------------------------------------------------------------------
# Proxy integration tests
# ---------------------------------------------------------------------------


class TestPodmanBackendCreateSessionWithProxy:
    """Tests for create_session with domain filtering (proxy setup)."""

    @patch("paude.backends.podman.get_podman_machine_dns")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_creates_network_and_proxy(
        self, mock_runner_class: MagicMock, mock_dns: MagicMock
    ) -> None:
        """create_session with allowed_domains creates network and proxy."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_dns.return_value = None
        mock_network = MagicMock()

        backend = _make_backend(mock_runner, mock_network)

        config = SessionConfig(
            name="my-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            allowed_domains=[".googleapis.com", ".pypi.org"],
            proxy_image="proxy:latest",
        )
        session = backend.create_session(config)

        # Network should be created
        mock_network.create_internal_network.assert_called_once_with(
            "paude-net-my-session"
        )

        # Proxy container should be created
        mock_runner.create_session_proxy.assert_called_once_with(
            name="paude-proxy-my-session",
            image="proxy:latest",
            network="paude-net-my-session",
            dns=None,
            allowed_domains=[".googleapis.com", ".pypi.org"],
        )

        # Main container should be on the internal network
        call_kwargs = mock_runner.create_container.call_args[1]
        assert call_kwargs["network"] == "paude-net-my-session"

        # Main container should have proxy env vars
        env = call_kwargs["env"]
        assert env["HTTP_PROXY"] == "http://paude-proxy-my-session:3128"
        assert env["HTTPS_PROXY"] == "http://paude-proxy-my-session:3128"

        assert session.status == "stopped"

    @patch("paude.backends.podman.get_podman_machine_dns")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_stores_domains_in_labels(
        self, mock_runner_class: MagicMock, mock_dns: MagicMock
    ) -> None:
        """create_session stores allowed_domains in container labels."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_dns.return_value = None

        backend = _make_backend(mock_runner, MagicMock())

        config = SessionConfig(
            name="my-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            allowed_domains=[".googleapis.com", ".pypi.org"],
            proxy_image="proxy:latest",
        )
        backend.create_session(config)

        call_kwargs = mock_runner.create_container.call_args[1]
        labels = call_kwargs["labels"]
        assert PAUDE_LABEL_DOMAINS in labels
        assert labels[PAUDE_LABEL_DOMAINS] == ".googleapis.com,.pypi.org"
        assert labels[PAUDE_LABEL_PROXY_IMAGE] == "proxy:latest"

    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_without_domains_skips_proxy(
        self, mock_runner_class: MagicMock
    ) -> None:
        """create_session without allowed_domains does not create proxy."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_network = MagicMock()

        backend = _make_backend(mock_runner, mock_network)

        config = SessionConfig(
            name="my-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            allowed_domains=None,  # Unrestricted
        )
        backend.create_session(config)

        # No network or proxy created
        mock_network.create_internal_network.assert_not_called()
        mock_runner.create_session_proxy.assert_not_called()

        # Main container should NOT have proxy env vars
        call_kwargs = mock_runner.create_container.call_args[1]
        env = call_kwargs["env"]
        assert "HTTP_PROXY" not in env
        assert "HTTPS_PROXY" not in env
        assert call_kwargs["network"] is None

    @patch("paude.backends.podman.get_podman_machine_dns")
    @patch("paude.backends.podman.ContainerRunner")
    def test_create_session_cleans_up_proxy_on_container_failure(
        self, mock_runner_class: MagicMock, mock_dns: MagicMock
    ) -> None:
        """create_session cleans up proxy and network if container creation fails."""
        mock_runner = MagicMock()
        mock_runner.container_exists.return_value = False
        mock_runner.create_container.side_effect = RuntimeError("Container failed")
        mock_runner_class.return_value = mock_runner
        mock_dns.return_value = None
        mock_network = MagicMock()
        mock_volume = MagicMock()

        backend = _make_backend(mock_runner, mock_network, mock_volume)

        config = SessionConfig(
            name="my-session",
            workspace=Path("/home/user/project"),
            image="paude:latest",
            allowed_domains=[".googleapis.com"],
            proxy_image="proxy:latest",
        )

        with pytest.raises(RuntimeError):
            backend.create_session(config)

        # Proxy container should be cleaned up
        mock_runner.remove_container.assert_called_once_with(
            "paude-proxy-my-session", force=True
        )
        mock_network.remove_network.assert_called_once_with("paude-net-my-session")
        mock_volume.remove_volume.assert_called_once()


class TestPodmanBackendStartSessionWithProxy:
    """Tests for start_session proxy lifecycle."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_starts_proxy_before_main(
        self, mock_runner_class: MagicMock
    ) -> None:
        """start_session starts proxy container before main container."""
        mock_runner = MagicMock()
        # Both main and proxy containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

        backend.start_session("my-session")

        # Proxy should be started before main container
        mock_runner.start_session_proxy.assert_called_once_with(
            "paude-proxy-my-session"
        )
        mock_runner.start_container.assert_called_once_with("paude-my-session")

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_skips_proxy_when_absent(
        self, mock_runner_class: MagicMock
    ) -> None:
        """start_session skips proxy start when no proxy container exists."""
        mock_runner = MagicMock()
        # Only main container exists
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

        backend.start_session("my-session")

        mock_runner.start_session_proxy.assert_not_called()
        mock_runner.start_container.assert_called_once()


class TestPodmanBackendStopSessionWithProxy:
    """Tests for stop_session proxy lifecycle."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_session_stops_proxy_after_main(
        self, mock_runner_class: MagicMock
    ) -> None:
        """stop_session stops proxy after stopping main container."""
        mock_runner = MagicMock()
        # Both containers exist and running
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)

        backend.stop_session("my-session")

        mock_runner.stop_container_graceful.assert_called_once_with("paude-my-session")
        mock_runner.stop_container.assert_called_once_with("paude-proxy-my-session")


class TestPodmanBackendDeleteSessionWithProxy:
    """Tests for delete_session proxy cleanup."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_delete_session_removes_proxy_and_network(
        self, mock_runner_class: MagicMock
    ) -> None:
        """delete_session removes proxy container and network."""
        mock_runner = MagicMock()
        # Both containers exist
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False
        mock_runner_class.return_value = mock_runner
        mock_network = MagicMock()

        backend = _make_backend(mock_runner, mock_network)

        backend.delete_session("my-session", confirm=True)

        # Should remove both containers
        assert mock_runner.remove_container.call_count == 2
        remove_calls = [c[0][0] for c in mock_runner.remove_container.call_args_list]
        assert "paude-proxy-my-session" in remove_calls
        assert "paude-my-session" in remove_calls

        # Should remove network
        mock_network.remove_network.assert_called_once_with("paude-net-my-session")


class TestProxyHealthCheck:
    """Tests for _check_proxy_health and degraded status detection."""

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_shows_degraded_when_proxy_missing(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Running session with expected but missing proxy shows degraded."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                },
            }
        ]
        # Proxy container does not exist
        mock_runner.container_exists.side_effect = (
            lambda name: name != "paude-proxy-my-session"
        )
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].status == "degraded"

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_shows_degraded_when_proxy_stopped(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Running session with stopped proxy shows degraded."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                },
            }
        ]
        # Proxy exists but not running
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.side_effect = (
            lambda name: name != "paude-proxy-my-session"
        )
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].status == "degraded"

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_running_when_proxy_healthy(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Running session with healthy proxy shows running."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                },
            }
        ]
        # Proxy exists and running
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].status == "running"

    @patch("paude.backends.podman.ContainerRunner")
    def test_list_sessions_running_without_proxy_config(
        self, mock_runner_class: MagicMock
    ) -> None:
        """Running session without proxy config shows running (no proxy expected)."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        sessions = backend.list_sessions()

        assert len(sessions) == 1
        assert sessions[0].status == "running"

    @patch("paude.backends.podman.ContainerRunner")
    def test_get_session_shows_degraded_when_proxy_missing(
        self, mock_runner_class: MagicMock
    ) -> None:
        """get_session returns degraded when proxy is missing."""
        mock_runner = MagicMock()
        mock_runner.container_exists.side_effect = (
            lambda name: name != "paude-proxy-my-session"
        )
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    "paude.io/created-at": "2024-01-15T10:00:00Z",
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        session = backend.get_session("my-session")

        assert session is not None
        assert session.status == "degraded"


class TestProxyRecreation:
    """Tests for proxy recreation when proxy is missing but expected."""

    @patch("paude.backends.podman.get_podman_machine_dns")
    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_recreates_missing_proxy(
        self, mock_runner_class: MagicMock, mock_dns: MagicMock
    ) -> None:
        """start_session recreates proxy when missing but configured in labels."""
        mock_runner = MagicMock()
        mock_dns.return_value = None
        # Main container exists but proxy does not
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "exited",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    PAUDE_LABEL_DOMAINS: "api.example.com,cdn.example.com",
                    PAUDE_LABEL_PROXY_IMAGE: "paude-proxy:latest",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner
        mock_network = MagicMock()

        backend = _make_backend(mock_runner, mock_network)
        backend.start_session("my-session")

        # Should recreate the proxy
        mock_runner.create_session_proxy.assert_called_once_with(
            name="paude-proxy-my-session",
            image="paude-proxy:latest",
            network="paude-net-my-session",
            dns=None,
            allowed_domains=["api.example.com", "cdn.example.com"],
        )
        mock_runner.start_session_proxy.assert_called_once_with(
            "paude-proxy-my-session"
        )

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_skips_recreate_without_labels(
        self, mock_runner_class: MagicMock
    ) -> None:
        """start_session does not recreate proxy when no domain labels."""
        mock_runner = MagicMock()
        # Only main container exists, no proxy
        mock_runner.container_exists.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.get_container_state.return_value = "exited"
        mock_runner.attach_container.return_value = 0
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "exited",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                },
            }
        ]
        mock_runner_class.return_value = mock_runner

        backend = _make_backend(mock_runner)
        backend.start_session("my-session")

        mock_runner.create_session_proxy.assert_not_called()
        mock_runner.start_session_proxy.assert_not_called()

    @patch("paude.backends.podman.get_podman_machine_dns")
    @patch("paude.backends.podman.ContainerRunner")
    def test_connect_session_recreates_missing_proxy(
        self, mock_runner_class: MagicMock, mock_dns: MagicMock
    ) -> None:
        """connect_session recreates proxy when missing but configured."""
        mock_runner = MagicMock()
        mock_dns.return_value = None

        def container_exists(name: str) -> bool:
            return name == "paude-my-session"

        mock_runner.container_exists.side_effect = container_exists
        mock_runner.container_running.side_effect = (
            lambda name: name == "paude-my-session"
        )
        mock_runner.attach_container.return_value = 0
        mock_runner.exec_in_container.return_value = MagicMock(returncode=0)
        mock_runner.list_containers.return_value = [
            {
                "Names": ["paude-my-session"],
                "State": "running",
                "Labels": {
                    "paude.io/session-name": "my-session",
                    "paude.io/workspace": encode_path(Path("/project")),
                    PAUDE_LABEL_DOMAINS: "api.example.com",
                    PAUDE_LABEL_PROXY_IMAGE: "paude-proxy:latest",
                },
            }
        ]
        mock_runner_class.return_value = mock_runner
        mock_network = MagicMock()

        backend = _make_backend(mock_runner, mock_network)
        backend.connect_session("my-session")

        # Should recreate the proxy
        mock_runner.create_session_proxy.assert_called_once()
        mock_runner.start_session_proxy.assert_called_once()


class TestFindContainerBySessionName:
    """Tests for PodmanBackend._find_container_by_session_name."""

    def test_returns_matching_container(self) -> None:
        """Returns the container dict when session name matches."""
        mock_runner = MagicMock()
        encoded_workspace = encode_path(Path("/home/user/project"))
        container = {
            "Id": "abc123",
            "Labels": {
                "app": "paude",
                PAUDE_LABEL_SESSION: "my-session",
                PAUDE_LABEL_WORKSPACE: encoded_workspace,
                PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
            },
            "State": "running",
        }
        mock_runner.list_containers.return_value = [container]

        backend = _make_backend(mock_runner)
        result = backend._find_container_by_session_name("my-session")

        assert result is container
        mock_runner.list_containers.assert_called_once_with(
            label_filter=PAUDE_LABEL_APP
        )

    def test_returns_none_when_not_found(self) -> None:
        """Returns None when no container has the given session name."""
        mock_runner = MagicMock()
        container = {
            "Id": "abc123",
            "Labels": {
                "app": "paude",
                PAUDE_LABEL_SESSION: "other-session",
            },
            "State": "running",
        }
        mock_runner.list_containers.return_value = [container]

        backend = _make_backend(mock_runner)
        result = backend._find_container_by_session_name("my-session")

        assert result is None


class TestBuildSessionFromContainer:
    """Tests for PodmanBackend._build_session_from_container."""

    def test_constructs_session_correctly(self) -> None:
        """Builds a Session with correct fields from container dict."""
        mock_runner = MagicMock()
        encoded_workspace = encode_path(Path("/home/user/project"))
        container = {
            "Id": "abc123",
            "Labels": {
                "app": "paude",
                PAUDE_LABEL_SESSION: "my-session",
                PAUDE_LABEL_WORKSPACE: encoded_workspace,
                PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
            },
            "State": "running",
        }
        mock_runner.container_exists.return_value = False

        backend = _make_backend(mock_runner)
        session = backend._build_session_from_container("my-session", container)

        assert session.name == "my-session"
        assert session.workspace == Path("/home/user/project")
        assert session.created_at == "2026-01-01T00:00:00+00:00"
        assert session.status == "running"
        assert session.backend_type == "podman"
        assert session.container_id == "abc123"
        assert session.volume_name == "paude-my-session-workspace"

    def test_handles_missing_workspace_label(self) -> None:
        """Falls back to Path('/') when workspace label is missing."""
        mock_runner = MagicMock()
        container = {
            "Id": "def456",
            "Labels": {
                "app": "paude",
                PAUDE_LABEL_SESSION: "my-session",
                PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
            },
            "State": "exited",
        }
        mock_runner.container_exists.return_value = False

        backend = _make_backend(mock_runner)
        session = backend._build_session_from_container("my-session", container)

        assert session.workspace == Path("/")
        assert session.status == "stopped"

    def test_includes_proxy_health_check(self) -> None:
        """Status is degraded when proxy is expected but missing."""
        mock_runner = MagicMock()
        encoded_workspace = encode_path(Path("/home/user/project"))
        container = {
            "Id": "abc123",
            "Labels": {
                "app": "paude",
                PAUDE_LABEL_SESSION: "my-session",
                PAUDE_LABEL_WORKSPACE: encoded_workspace,
                PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
                PAUDE_LABEL_DOMAINS: "example.com",
            },
            "State": "running",
        }
        # Proxy container does not exist
        mock_runner.container_exists.return_value = False

        backend = _make_backend(mock_runner)
        session = backend._build_session_from_container("my-session", container)

        assert session.status == "degraded"
