"""Tests for the backends module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from paude.backends import PodmanBackend, Session


class TestSession:
    """Tests for Session dataclass."""

    def test_session_creation(self) -> None:
        """Session can be created with all required fields."""
        session = Session(
            name="test-123",
            status="running",
            workspace=Path("/test/workspace"),
            created_at="2024-01-15T10:00:00Z",
            backend_type="podman",
        )

        assert session.name == "test-123"
        assert session.status == "running"
        assert session.workspace == Path("/test/workspace")
        assert session.created_at == "2024-01-15T10:00:00Z"
        assert session.backend_type == "podman"

    def test_session_status_values(self) -> None:
        """Session can have various status values."""
        for status in ["running", "stopped", "error", "pending"]:
            session = Session(
                name="test",
                status=status,
                workspace=Path("/test"),
                created_at="2024-01-15T10:00:00Z",
                backend_type="podman",
            )
            assert session.status == status


class TestPodmanBackend:
    """Tests for PodmanBackend class."""

    def test_instantiation(self) -> None:
        """PodmanBackend can be instantiated."""
        backend = PodmanBackend()
        assert backend is not None

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_legacy_returns_session(self, mock_runner_class: MagicMock) -> None:
        """start_session_legacy returns a Session object."""
        mock_runner = MagicMock()
        mock_runner.run_claude.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.start_session_legacy(
            image="test-image:latest",
            workspace=Path("/test/workspace"),
            env={"TEST": "value"},
            mounts=["-v", "/host:/container"],
            args=["--help"],
            workdir="/test/workspace",
            network_restricted=True,
            yolo=False,
            network=None,
        )

        assert isinstance(session, Session)
        assert session.backend_type == "podman"
        assert session.workspace == Path("/test/workspace")

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_legacy_status_stopped_on_success(
        self, mock_runner_class: MagicMock
    ) -> None:
        """start_session_legacy returns stopped status when exit code is 0."""
        mock_runner = MagicMock()
        mock_runner.run_claude.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.start_session_legacy(
            image="test-image:latest",
            workspace=Path("/test/workspace"),
            env={},
            mounts=[],
            args=[],
        )

        assert session.status == "stopped"

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_legacy_status_error_on_failure(
        self, mock_runner_class: MagicMock
    ) -> None:
        """start_session_legacy returns error status when exit code is non-zero."""
        mock_runner = MagicMock()
        mock_runner.run_claude.return_value = 1
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        session = backend.start_session_legacy(
            image="test-image:latest",
            workspace=Path("/test/workspace"),
            env={},
            mounts=[],
            args=[],
        )

        assert session.status == "error"

    @patch("paude.backends.podman.ContainerRunner")
    def test_start_session_legacy_calls_run_claude(self, mock_runner_class: MagicMock) -> None:
        """start_session_legacy calls runner.run_claude with correct args."""
        mock_runner = MagicMock()
        mock_runner.run_claude.return_value = 0
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.start_session_legacy(
            image="test-image:latest",
            workspace=Path("/test/workspace"),
            env={"KEY": "value"},
            mounts=["-v", "/a:/b"],
            args=["--help"],
            workdir="/work",
            network_restricted=False,
            yolo=True,
            network="test-network",
        )

        mock_runner.run_claude.assert_called_once_with(
            image="test-image:latest",
            mounts=["-v", "/a:/b"],
            env={"KEY": "value"},
            args=["--help"],
            workdir="/work",
            network="test-network",
            yolo=True,
            allow_network=True,
        )

    def test_attach_session_legacy_returns_error(self) -> None:
        """attach_session_legacy returns 1 for Podman (legacy sessions are ephemeral)."""
        backend = PodmanBackend()
        result = backend.attach_session_legacy("any-id")
        assert result == 1

    def test_stop_session_legacy_is_noop(self) -> None:
        """stop_session_legacy does nothing for Podman legacy sessions."""
        backend = PodmanBackend()
        backend.stop_session_legacy("any-id")

    def test_list_sessions_returns_empty(self) -> None:
        """list_sessions returns empty list for Podman."""
        backend = PodmanBackend()
        sessions = backend.list_sessions()
        assert sessions == []

    def test_sync_workspace_legacy_is_noop(self) -> None:
        """sync_workspace_legacy does nothing for Podman."""
        backend = PodmanBackend()
        backend.sync_workspace_legacy("any-id", "both")

    @patch("paude.backends.podman.ContainerRunner")
    def test_run_proxy_delegates_to_runner(
        self, mock_runner_class: MagicMock
    ) -> None:
        """run_proxy calls runner.run_proxy."""
        mock_runner = MagicMock()
        mock_runner.run_proxy.return_value = "proxy-container-123"
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        result = backend.run_proxy("proxy:latest", "network-name", "1.2.3.4")

        mock_runner.run_proxy.assert_called_once_with(
            "proxy:latest", "network-name", "1.2.3.4"
        )
        assert result == "proxy-container-123"

    @patch("paude.backends.podman.ContainerRunner")
    def test_stop_container_delegates_to_runner(
        self, mock_runner_class: MagicMock
    ) -> None:
        """stop_container calls runner.stop_container."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        backend.stop_container("container-name")

        mock_runner.stop_container.assert_called_once_with("container-name")

    @patch("paude.backends.podman.ContainerRunner")
    def test_run_post_create_delegates_to_runner(
        self, mock_runner_class: MagicMock
    ) -> None:
        """run_post_create calls runner.run_post_create."""
        mock_runner = MagicMock()
        mock_runner.run_post_create.return_value = True
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner

        result = backend.run_post_create(
            image="image:tag",
            mounts=["-v", "/a:/b"],
            env={"KEY": "value"},
            command="echo hello",
            workdir="/work",
            network="network-name",
        )

        mock_runner.run_post_create.assert_called_once_with(
            image="image:tag",
            mounts=["-v", "/a:/b"],
            env={"KEY": "value"},
            command="echo hello",
            workdir="/work",
            network="network-name",
        )
        assert result is True


class TestBackendProtocol:
    """Tests for Backend Protocol conformance."""

    def test_podman_backend_implements_protocol(self) -> None:
        """PodmanBackend implements all required Backend methods."""
        backend = PodmanBackend()

        # New Backend protocol methods
        assert hasattr(backend, "create_session")
        assert hasattr(backend, "delete_session")
        assert hasattr(backend, "start_session")
        assert hasattr(backend, "stop_session")
        assert hasattr(backend, "connect_session")
        assert hasattr(backend, "list_sessions")
        assert hasattr(backend, "sync_session")
        assert hasattr(backend, "get_session")

        assert callable(backend.create_session)
        assert callable(backend.delete_session)
        assert callable(backend.start_session)
        assert callable(backend.stop_session)
        assert callable(backend.connect_session)
        assert callable(backend.list_sessions)
        assert callable(backend.sync_session)
        assert callable(backend.get_session)

    def test_podman_backend_implements_legacy_protocol(self) -> None:
        """PodmanBackend implements all required LegacyBackend methods."""
        backend = PodmanBackend()

        # Legacy protocol methods
        assert hasattr(backend, "start_session_legacy")
        assert hasattr(backend, "attach_session_legacy")
        assert hasattr(backend, "stop_session_legacy")
        assert hasattr(backend, "list_sessions")
        assert hasattr(backend, "sync_workspace_legacy")

        assert callable(backend.start_session_legacy)
        assert callable(backend.attach_session_legacy)
        assert callable(backend.stop_session_legacy)
        assert callable(backend.list_sessions)
        assert callable(backend.sync_workspace_legacy)
