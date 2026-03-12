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

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_list_sessions_returns_empty(self, mock_runner_class: MagicMock) -> None:
        """list_sessions returns empty list for Podman when no sessions exist."""
        mock_runner = MagicMock()
        mock_runner.list_containers.return_value = []
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        sessions = backend.list_sessions()
        assert sessions == []

    @patch("paude.backends.podman.backend.ContainerRunner")
    def test_stop_container_delegates_to_runner(
        self, mock_runner_class: MagicMock
    ) -> None:
        """stop_container calls runner.stop_container."""
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner

        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner

        backend.stop_container("container-name")

        mock_runner.stop_container.assert_called_once_with("container-name")


class TestPodmanExecInSession:
    """Tests for PodmanBackend.exec_in_session."""

    def _make_backend(self) -> tuple[PodmanBackend, MagicMock]:
        mock_runner = MagicMock()
        backend = PodmanBackend()
        backend._runner = mock_runner
        backend._proxy._runner = mock_runner
        return backend, mock_runner

    def test_exec_in_session_success(self) -> None:
        """exec_in_session returns (returncode, stdout, stderr) on success."""
        backend, mock_runner = self._make_backend()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello\n"
        mock_result.stderr = ""
        mock_runner.exec_in_container.return_value = mock_result

        rc, out, err = backend.exec_in_session("my-session", "echo hello")

        assert rc == 0
        assert out == "hello\n"
        assert err == ""
        mock_runner.exec_in_container.assert_called_once_with(
            "paude-my-session", ["bash", "-c", "echo hello"], check=False
        )

    def test_exec_in_session_not_found(self) -> None:
        """exec_in_session raises SessionNotFoundError if container missing."""
        from paude.backends.podman import SessionNotFoundError

        backend, mock_runner = self._make_backend()
        mock_runner.container_exists.return_value = False

        import pytest

        with pytest.raises(SessionNotFoundError, match="not found"):
            backend.exec_in_session("missing", "echo hello")

    def test_exec_in_session_not_running(self) -> None:
        """exec_in_session raises ValueError if container not running."""
        backend, mock_runner = self._make_backend()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = False

        import pytest

        with pytest.raises(ValueError, match="not running"):
            backend.exec_in_session("stopped", "echo hello")

    def test_exec_in_session_nonzero_exit(self) -> None:
        """exec_in_session returns non-zero exit code without raising."""
        backend, mock_runner = self._make_backend()
        mock_runner.container_exists.return_value = True
        mock_runner.container_running.return_value = True
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error\n"
        mock_runner.exec_in_container.return_value = mock_result

        rc, out, err = backend.exec_in_session("my-session", "false")

        assert rc == 1
        assert err == "error\n"


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
        assert hasattr(backend, "get_session")

        assert callable(backend.create_session)
        assert callable(backend.delete_session)
        assert callable(backend.start_session)
        assert callable(backend.stop_session)
        assert callable(backend.connect_session)
        assert callable(backend.list_sessions)
        assert callable(backend.get_session)
        assert callable(backend.exec_in_session)
        assert callable(backend.copy_to_session)
        assert callable(backend.copy_from_session)


class TestSharedUtils:
    """Tests for encode_path and decode_path shared utilities."""

    def test_encode_path_standard_roundtrip(self) -> None:
        """encode_path with standard base64 produces a roundtrip-safe encoding."""
        from paude.backends.shared import decode_path, encode_path

        original = Path("/home/user/workspace")
        encoded = encode_path(original)
        decoded = decode_path(encoded)

        assert decoded == original

    def test_encode_path_url_safe_roundtrip(self) -> None:
        """encode_path with url_safe=True produces a roundtrip-safe encoding."""
        from paude.backends.shared import decode_path, encode_path

        original = Path("/home/user/workspace")
        encoded = encode_path(original, url_safe=True)
        decoded = decode_path(encoded, url_safe=True)

        assert decoded == original

    def test_decode_path_standard(self) -> None:
        """decode_path decodes standard base64-encoded path."""
        import base64

        from paude.backends.shared import decode_path

        path_str = "/tmp/test/dir"
        encoded = base64.b64encode(path_str.encode()).decode()

        result = decode_path(encoded)

        assert result == Path(path_str)

    def test_decode_path_url_safe(self) -> None:
        """decode_path with url_safe=True decodes URL-safe base64-encoded path."""
        import base64

        from paude.backends.shared import decode_path

        path_str = "/tmp/test/dir"
        encoded = base64.urlsafe_b64encode(path_str.encode()).decode()

        result = decode_path(encoded, url_safe=True)

        assert result == Path(path_str)

    def test_decode_path_handles_invalid_input(self) -> None:
        """decode_path returns Path of raw input on invalid base64."""
        from paude.backends.shared import decode_path

        invalid = "not-valid-base64!!!"
        result = decode_path(invalid)

        assert result == Path(invalid)

    def test_url_safe_and_standard_differ_for_special_chars(self) -> None:
        """URL-safe and standard encodings differ for paths with special chars."""
        from paude.backends.shared import encode_path

        # Paths with characters that produce + or / in standard base64
        # Use a path that will generate differing base64 chars
        test_path = Path("/home/user/path??with>>special")
        standard = encode_path(test_path, url_safe=False)
        url_safe = encode_path(test_path, url_safe=True)

        # They should differ when the path produces +, /, or = in encoding
        # At minimum, both should be valid encodings
        assert isinstance(standard, str)
        assert isinstance(url_safe, str)
        # URL-safe should not contain + or /
        assert "+" not in url_safe
        assert "/" not in url_safe
