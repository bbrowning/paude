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


class TestFindWorkspaceSession:
    """Tests for find_workspace_session."""

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_returns_podman_workspace_session_when_found(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns Podman workspace session when found."""
        from paude.session_discovery import find_workspace_session

        podman_session = _make_session(
            "podman-ws", workspace=Path("/my/workspace"), backend_type="podman"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = podman_session
        mock_podman_class.return_value = mock_podman

        result = find_workspace_session()

        assert result is not None
        session, backend = result
        assert session.name == "podman-ws"
        assert backend is mock_podman
        # OpenShift should not be checked
        mock_os_backend_class.assert_not_called()

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_returns_openshift_workspace_session_when_podman_has_none(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns OpenShift workspace session when Podman has none."""
        from paude.session_discovery import find_workspace_session

        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        os_session = _make_session(
            "os-ws", workspace=Path("/my/workspace"), backend_type="openshift"
        )
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = os_session
        mock_os_backend_class.return_value = mock_os_backend

        result = find_workspace_session()

        assert result is not None
        session, backend = result
        assert session.name == "os-ws"
        assert backend is mock_os_backend

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_returns_none_when_neither_backend_has_workspace_session(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns None when neither backend has a workspace session."""
        from paude.session_discovery import find_workspace_session

        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend_class.return_value = mock_os_backend

        result = find_workspace_session()

        assert result is None

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_with_status_filter_skips_stopped_workspace_session(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """With status_filter='running', skips stopped workspace sessions."""
        from paude.session_discovery import find_workspace_session

        stopped_session = _make_session(
            "stopped-ws",
            status="stopped",
            workspace=Path("/my/workspace"),
            backend_type="podman",
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = stopped_session
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend_class.return_value = mock_os_backend

        result = find_workspace_session(status_filter="running")

        assert result is None

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_without_status_filter_returns_session_regardless_of_status(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Without status_filter, returns session regardless of status."""
        from paude.session_discovery import find_workspace_session

        stopped_session = _make_session(
            "stopped-ws",
            status="stopped",
            workspace=Path("/my/workspace"),
            backend_type="podman",
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = stopped_session
        mock_podman_class.return_value = mock_podman

        result = find_workspace_session()

        assert result is not None
        session, backend = result
        assert session.name == "stopped-ws"
        assert session.status == "stopped"

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_handles_podman_unavailable_gracefully(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Handles Podman unavailable gracefully and falls through to OpenShift."""
        from paude.session_discovery import find_workspace_session

        mock_podman_class.side_effect = Exception("podman not found")

        os_session = _make_session(
            "os-ws", workspace=Path("/my/workspace"), backend_type="openshift"
        )
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = os_session
        mock_os_backend_class.return_value = mock_os_backend

        result = find_workspace_session()

        assert result is not None
        session, backend = result
        assert session.name == "os-ws"

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_handles_openshift_unavailable_gracefully(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Handles OpenShift unavailable gracefully when Podman also has nothing."""
        from paude.session_discovery import find_workspace_session

        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        mock_os_backend_class.side_effect = Exception("oc not found")

        result = find_workspace_session()

        assert result is None

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_prefers_podman_over_openshift(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Prefers Podman over OpenShift when both have workspace match."""
        from paude.session_discovery import find_workspace_session

        podman_session = _make_session(
            "podman-ws", workspace=Path("/my/workspace"), backend_type="podman"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = podman_session
        mock_podman_class.return_value = mock_podman

        os_session = _make_session(
            "os-ws", workspace=Path("/my/workspace"), backend_type="openshift"
        )
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = os_session
        mock_os_backend_class.return_value = mock_os_backend

        result = find_workspace_session()

        assert result is not None
        session, backend = result
        assert session.name == "podman-ws"
        assert backend is mock_podman
        # OpenShift should not even be checked
        mock_os_backend_class.assert_not_called()


# collect_all_sessions tests


class TestCollectAllSessions:
    """Tests for collect_all_sessions."""

    @pytest.fixture(autouse=True)
    def _mock_docker_engine(self):
        """Block Docker backend creation in collect_all_sessions."""
        with patch(
            "paude.session_discovery.ContainerEngine",
            side_effect=Exception("docker not available"),
        ):
            yield

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_collects_from_both_backends(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Collects sessions from both Podman and OpenShift backends."""
        from paude.session_discovery import collect_all_sessions

        podman_session = _make_session("podman-s1", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-s1", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result, reachable = collect_all_sessions()

        assert len(result) == 2
        names = [s.name for s, _ in result]
        assert "podman-s1" in names
        assert "os-s1" in names
        assert reachable == {"podman", "openshift"}

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_filters_by_status(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Filters sessions by status when status_filter is set."""
        from paude.session_discovery import collect_all_sessions

        running_session = _make_session(
            "running-s", status="running", backend_type="podman"
        )
        stopped_session = _make_session(
            "stopped-s", status="stopped", backend_type="podman"
        )
        mock_podman = MagicMock()
        mock_podman.list_sessions.return_value = [running_session, stopped_session]
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result, reachable = collect_all_sessions(status_filter="running")

        assert len(result) == 1
        assert result[0][0].name == "running-s"
        assert "podman" in reachable

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_returns_empty_list_when_no_sessions(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns empty list when no sessions exist in any backend."""
        from paude.session_discovery import collect_all_sessions

        mock_podman = MagicMock()
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result, reachable = collect_all_sessions()

        assert result == []
        assert reachable == {"podman", "openshift"}

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_handles_podman_unavailable_gracefully(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Handles Podman unavailable gracefully and still collects from OpenShift."""
        from paude.session_discovery import collect_all_sessions

        mock_podman_class.side_effect = Exception("podman not found")

        os_session = _make_session("os-s1", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result, reachable = collect_all_sessions()

        assert len(result) == 1
        assert result[0][0].name == "os-s1"
        assert reachable == {"openshift"}

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_handles_openshift_unavailable_gracefully(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Handles OpenShift unavailable gracefully and still collects from Podman."""
        from paude.session_discovery import collect_all_sessions

        podman_session = _make_session("podman-s1", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        mock_os_backend_class.side_effect = Exception("oc not found")

        result, reachable = collect_all_sessions()

        assert len(result) == 1
        assert result[0][0].name == "podman-s1"
        assert reachable == {"podman"}

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_uses_pre_created_backend_instances(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Uses pre-created backend instances when provided."""
        from paude.session_discovery import collect_all_sessions

        podman_session = _make_session("podman-s1", backend_type="podman")
        pre_podman = MagicMock()
        pre_podman.list_sessions.return_value = [podman_session]

        os_session = _make_session("os-s1", backend_type="openshift")
        pre_os = MagicMock()
        pre_os.list_sessions.return_value = [os_session]

        result, reachable = collect_all_sessions(
            podman_backend=pre_podman, os_backend=pre_os
        )

        assert len(result) == 2
        assert reachable == {"podman", "openshift"}
        # Should NOT have created new backends
        mock_podman_class.assert_not_called()
        mock_os_backend_class.assert_not_called()

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.PodmanBackend")
    def test_creates_openshift_backend_when_not_provided(
        self,
        mock_podman_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Creates OpenShift backend when not provided as pre-created instance."""
        from paude.session_discovery import collect_all_sessions

        mock_podman = MagicMock()
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-s1", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result, reachable = collect_all_sessions()

        assert len(result) == 1
        assert result[0][0].name == "os-s1"
        assert reachable == {"podman", "openshift"}
        mock_os_backend_class.assert_called_once()


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


# create_openshift_backend tests


class TestCreateOpenshiftBackend:
    """Tests for create_openshift_backend."""

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    def test_returns_backend_instance_when_available(
        self,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns an OpenShiftBackend instance when OpenShift is available."""
        from paude.session_discovery import create_openshift_backend

        mock_os_backend = MagicMock()
        mock_os_backend_class.return_value = mock_os_backend

        result = create_openshift_backend()

        assert result is mock_os_backend

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    def test_returns_none_when_openshift_unavailable(
        self,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Returns None when OpenShift is unavailable."""
        from paude.session_discovery import create_openshift_backend

        mock_os_backend_class.side_effect = Exception("oc not found")

        result = create_openshift_backend()

        assert result is None

    @patch("paude.session_discovery.OpenShiftConfig")
    @patch("paude.session_discovery.OpenShiftBackend")
    def test_passes_context_and_namespace_to_config(
        self,
        mock_os_backend_class: MagicMock,
        mock_os_config_class: MagicMock,
    ):
        """Passes context and namespace through to OpenShiftConfig."""
        from paude.session_discovery import create_openshift_backend

        mock_os_backend = MagicMock()
        mock_os_backend_class.return_value = mock_os_backend

        create_openshift_backend(
            openshift_context="my-context",
            openshift_namespace="my-namespace",
        )

        mock_os_config_class.assert_called_once_with(
            context="my-context",
            namespace="my-namespace",
        )


class TestSshSessionDiscovery:
    """Tests for SSH session discovery from the local registry."""

    @patch(
        "paude.session_discovery.ContainerEngine", side_effect=Exception("no engine")
    )
    @patch("paude.session_discovery.PodmanBackend", side_effect=Exception("no podman"))
    @patch("paude.session_discovery.create_openshift_backend", return_value=None)
    @patch("paude.registry.SessionRegistry")
    def test_find_workspace_session_checks_ssh_registry(
        self,
        mock_registry_cls,
        mock_os,
        mock_podman,
        mock_engine,
    ):
        """find_workspace_session checks SSH sessions from registry."""
        from paude.session_discovery import find_workspace_session

        mock_entry = MagicMock()
        mock_entry.ssh_host = "user@remote"
        mock_entry.ssh_key = None
        mock_entry.engine = "docker"
        mock_entry.name = "ssh-session"
        mock_entry.workspace = str(Path.cwd())

        mock_registry = MagicMock()
        mock_registry.list_entries.return_value = [mock_entry]
        mock_registry_cls.return_value = mock_registry

        with patch("paude.session_discovery._build_ssh_backend") as mock_build:
            mock_backend = MagicMock()
            mock_session = MagicMock()
            mock_session.status = "running"
            mock_backend.get_session.return_value = mock_session
            mock_build.return_value = mock_backend

            result = find_workspace_session()

        assert result is not None
        assert result == (mock_session, mock_backend)

    @patch(
        "paude.session_discovery.ContainerEngine", side_effect=Exception("no engine")
    )
    @patch("paude.session_discovery.PodmanBackend", side_effect=Exception("no podman"))
    @patch("paude.session_discovery.create_openshift_backend", return_value=None)
    @patch("paude.registry.SessionRegistry")
    def test_find_workspace_session_skips_non_ssh_entries(
        self,
        mock_registry_cls,
        mock_os,
        mock_podman,
        mock_engine,
    ):
        """find_workspace_session skips registry entries without ssh_host."""
        from paude.session_discovery import find_workspace_session

        mock_entry = MagicMock()
        mock_entry.ssh_host = None  # Not an SSH session
        mock_entry.name = "local-session"

        mock_registry = MagicMock()
        mock_registry.list_entries.return_value = [mock_entry]
        mock_registry_cls.return_value = mock_registry

        result = find_workspace_session()
        assert result is None

    @patch(
        "paude.session_discovery.ContainerEngine", side_effect=Exception("no engine")
    )
    @patch("paude.session_discovery.PodmanBackend", side_effect=Exception("no podman"))
    @patch("paude.session_discovery.create_openshift_backend", return_value=None)
    @patch("paude.registry.SessionRegistry")
    def test_collect_all_sessions_includes_ssh(
        self,
        mock_registry_cls,
        mock_os,
        mock_podman,
        mock_engine,
    ):
        """collect_all_sessions includes SSH sessions from registry."""
        from paude.session_discovery import collect_all_sessions

        mock_entry = MagicMock()
        mock_entry.ssh_host = "user@remote"
        mock_entry.ssh_key = None
        mock_entry.engine = "docker"
        mock_entry.name = "ssh-session"

        mock_registry = MagicMock()
        mock_registry.list_entries.return_value = [mock_entry]
        mock_registry_cls.return_value = mock_registry

        with patch("paude.session_discovery._build_ssh_backend") as mock_build:
            mock_backend = MagicMock()
            mock_session = MagicMock()
            mock_session.name = "ssh-session"
            mock_session.status = "running"
            mock_backend.get_session.return_value = mock_session
            mock_build.return_value = mock_backend

            sessions, reachable = collect_all_sessions()

        assert len(sessions) == 1
        assert sessions[0] == (mock_session, mock_backend)
        assert "ssh" in reachable

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
