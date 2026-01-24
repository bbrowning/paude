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

    def test_list_sessions_returns_empty(self) -> None:
        """list_sessions returns empty list for Podman when no sessions exist."""
        backend = PodmanBackend()
        sessions = backend.list_sessions()
        assert sessions == []

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

        # Backend protocol methods
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
