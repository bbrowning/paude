"""Tests for session discovery helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import Session
from paude.session_discovery import _status_matches


class TestStatusMatches:
    """Tests for _status_matches helper."""

    def test_none_filter_matches_anything(self) -> None:
        assert _status_matches("running", None) is True
        assert _status_matches("stopped", None) is True
        assert _status_matches("degraded", None) is True

    def test_exact_match(self) -> None:
        assert _status_matches("running", "running") is True
        assert _status_matches("stopped", "stopped") is True

    def test_no_match(self) -> None:
        assert _status_matches("stopped", "running") is False
        assert _status_matches("error", "running") is False

    def test_degraded_matches_running(self) -> None:
        """Degraded sessions should match 'running' filter."""
        assert _status_matches("degraded", "running") is True

    def test_degraded_does_not_match_stopped(self) -> None:
        assert _status_matches("degraded", "stopped") is False


def _make_session(
    name: str,
    status: str = "running",
    workspace: Path | None = None,
    backend_type: str = "podman",
) -> Session:
    """Helper to create a Session object for tests."""
    return Session(
        name=name,
        status=status,
        workspace=workspace or Path("/some/path"),
        created_at="2024-01-15T10:00:00Z",
        backend_type=backend_type,
    )


# find_workspace_session tests


# collect_all_sessions tests


class TestCollectAllSessions:
    """Tests for collect_all_sessions."""

    @pytest.fixture(autouse=True)
    def _mock_docker_and_ssh(self):
        """Block Docker and SSH backends in collect_all_sessions."""
        with (
            patch(
                "paude.session_discovery._collect_docker_sessions",
                side_effect=Exception("docker not available"),
            ),
            patch(
                "paude.session_discovery._collect_ssh_sessions",
                return_value=[],
            ),
        ):
            yield


# resolve_session_for_backend tests


class TestResolveSessionForBackend:
    """Tests for resolve_session_for_backend."""

    @patch("paude.session_discovery.Path")
    def test_returns_workspace_matching_session_name(self, mock_path_class: MagicMock):
        """Returns the workspace-matching session name."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        workspace_session = _make_session(
            "ws-session", workspace=Path("/my/workspace"), backend_type="podman"
        )
        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = workspace_session

        result = resolve_session_for_backend(mock_backend)

        assert result == "ws-session"

    @patch("paude.session_discovery.Path")
    def test_returns_single_available_session_name_when_no_workspace_match(
        self, mock_path_class: MagicMock
    ):
        """Returns single available session name when no workspace match."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        single_session = _make_session("only-session", backend_type="podman")
        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = None
        mock_backend.list_sessions.return_value = [single_session]

        result = resolve_session_for_backend(mock_backend)

        assert result == "only-session"

    @patch("paude.session_discovery.typer")
    @patch("paude.session_discovery.Path")
    def test_returns_none_and_prints_error_when_no_sessions(
        self, mock_path_class: MagicMock, mock_typer: MagicMock
    ):
        """Returns None and prints helpful error when no sessions exist."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = None
        mock_backend.list_sessions.return_value = []

        result = resolve_session_for_backend(mock_backend)

        assert result is None
        mock_typer.echo.assert_called()

    @patch("paude.session_discovery.typer")
    @patch("paude.session_discovery.Path")
    def test_returns_none_and_prints_list_when_multiple_sessions(
        self, mock_path_class: MagicMock, mock_typer: MagicMock
    ):
        """Returns None and prints session list when multiple sessions exist."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        session1 = _make_session("session-1", backend_type="podman")
        session2 = _make_session("session-2", backend_type="podman")
        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = None
        mock_backend.list_sessions.return_value = [session1, session2]

        result = resolve_session_for_backend(mock_backend)

        assert result is None
        mock_typer.echo.assert_called()

    @patch("paude.session_discovery.Path")
    def test_respects_status_filter_on_workspace_match(
        self, mock_path_class: MagicMock
    ):
        """Workspace match must pass status_filter to be returned."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        stopped_session = _make_session(
            "stopped-ws", status="stopped", workspace=Path("/my/workspace")
        )
        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = stopped_session
        mock_backend.list_sessions.return_value = [stopped_session]

        result = resolve_session_for_backend(mock_backend, status_filter="running")

        # Workspace match is stopped, filter is "running", so it should not return it
        assert result is None

    @patch("paude.session_discovery.Path")
    def test_respects_status_filter_on_fallback_list(self, mock_path_class: MagicMock):
        """Fallback session list respects status_filter."""
        from paude.session_discovery import resolve_session_for_backend

        mock_path_class.cwd.return_value = Path("/my/workspace")

        running_session = _make_session("running-s", status="running")
        stopped_session = _make_session("stopped-s", status="stopped")
        mock_backend = MagicMock()
        mock_backend.find_session_for_workspace.return_value = None
        mock_backend.list_sessions.return_value = [running_session, stopped_session]

        result = resolve_session_for_backend(mock_backend, status_filter="running")

        assert result == "running-s"


class TestSshSessionDiscovery:
    """Tests for SSH session discovery from the local registry."""

    def test_build_ssh_backend_returns_none_for_no_ssh_host(self):
        """_build_ssh_backend returns None for entries without ssh_host."""
        from paude.registry import RegistryEntry
        from paude.session_discovery import _build_ssh_backend

        entry = RegistryEntry(
            name="local",
            backend_type="podman",
            workspace="/tmp/test",
            agent="claude",
            created_at="2024-01-01T00:00:00",
        )
        assert _build_ssh_backend(entry) is None

    @patch("paude.session_discovery._build_ssh_backend")
    @patch("paude.registry.SessionRegistry")
    def test_collect_ssh_sessions_handles_unreachable_host(
        self,
        mock_registry_cls,
        mock_build,
    ):
        """_collect_ssh_sessions skips unreachable hosts gracefully."""
        from paude.session_discovery import _collect_ssh_sessions

        reachable_entry = MagicMock()
        reachable_entry.ssh_host = "user@reachable"
        reachable_entry.name = "reachable-session"

        unreachable_entry = MagicMock()
        unreachable_entry.ssh_host = "user@unreachable"
        unreachable_entry.name = "unreachable-session"

        mock_registry = MagicMock()
        mock_registry.list_entries.return_value = [reachable_entry, unreachable_entry]
        mock_registry_cls.return_value = mock_registry

        reachable_backend = MagicMock()
        reachable_session = MagicMock()
        reachable_session.status = "running"
        reachable_backend.get_session.return_value = reachable_session

        unreachable_backend = MagicMock()
        unreachable_backend.get_session.side_effect = Exception("Connection refused")

        def build_side_effect(entry, **_kwargs):
            if entry.name == "reachable-session":
                return reachable_backend
            return unreachable_backend

        mock_build.side_effect = build_side_effect

        results = _collect_ssh_sessions()

        assert len(results) == 1
        assert results[0] == (reachable_session, reachable_backend)

    @patch("paude.session_discovery._build_ssh_backend")
    @patch("paude.registry.SessionRegistry")
    def test_collect_ssh_sessions_queries_all_entries(
        self,
        mock_registry_cls,
        mock_build,
    ):
        """_collect_ssh_sessions queries all SSH entries concurrently."""
        from paude.session_discovery import _collect_ssh_sessions

        entries = []
        backends = {}
        for i in range(3):
            entry = MagicMock()
            entry.ssh_host = f"user@host{i}"
            entry.name = f"session-{i}"
            entries.append(entry)

            backend = MagicMock()
            session = MagicMock()
            session.status = "running"
            backend.get_session.return_value = session
            backends[f"session-{i}"] = (backend, session)

        mock_registry = MagicMock()
        mock_registry.list_entries.return_value = entries
        mock_registry_cls.return_value = mock_registry

        def build_side_effect(entry, **_kwargs):
            return backends[entry.name][0]

        mock_build.side_effect = build_side_effect

        results = _collect_ssh_sessions()

        assert len(results) == 3
        # Verify all backends were queried
        for _name, (backend, _) in backends.items():
            backend.get_session.assert_called_once()
