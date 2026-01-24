"""Tests for CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from paude.backends import Session
from paude.cli import app

runner = CliRunner()


def test_help_shows_help():
    """--help shows help and exits 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "paude - Run Claude Code" in result.stdout


def test_short_help_shows_help():
    """-h shows help and exits 0."""
    result = runner.invoke(app, ["-h"])
    assert result.exit_code == 0
    assert "paude - Run Claude Code" in result.stdout


def test_version_shows_version():
    """--version shows version and exits 0."""
    from paude import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"paude {__version__}" in result.stdout


def test_short_version_shows_version():
    """-V shows version and exits 0."""
    from paude import __version__

    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert f"paude {__version__}" in result.stdout


def test_version_shows_development_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'development' when PAUDE_DEV=1."""
    monkeypatch.setenv("PAUDE_DEV", "1")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "development" in result.stdout
    assert "PAUDE_DEV=1" in result.stdout


def test_version_shows_installed_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'installed' when PAUDE_DEV=0."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "installed" in result.stdout
    assert "quay.io/bbrowning" in result.stdout


def test_version_shows_custom_registry(monkeypatch: pytest.MonkeyPatch):
    """--version shows custom registry when PAUDE_REGISTRY is set."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    monkeypatch.setenv("PAUDE_REGISTRY", "ghcr.io/custom")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ghcr.io/custom" in result.stdout


def test_dry_run_works():
    """--dry-run works and shows config info."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.stdout


def test_dry_run_shows_no_config(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """--dry-run shows 'none' when no config file exists."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "Configuration: none" in result.stdout


def test_dry_run_shows_flag_states():
    """--dry-run shows flag states."""
    result = runner.invoke(app, ["create", "--yolo", "--allow-network", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allow-network: True" in result.stdout


def test_yolo_flag_recognized():
    """--yolo flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--yolo", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout


def test_allow_network_flag_recognized():
    """--allow-network flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--allow-network", "--dry-run"])
    assert result.exit_code == 0
    assert "--allow-network: True" in result.stdout


def test_rebuild_flag_recognized():
    """--rebuild flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--rebuild", "--dry-run"])
    assert result.exit_code == 0
    assert "--rebuild: True" in result.stdout


def test_verbose_flag_recognized():
    """--verbose flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--verbose", "--dry-run"])
    assert result.exit_code == 0
    assert "--verbose: True" in result.stdout


def test_help_shows_dry_run_option():
    """--help shows --dry-run option."""
    result = runner.invoke(app, ["--help"])
    assert "--dry-run" in result.stdout


def test_args_option():
    """--args option is parsed and captured in claude_args (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--dry-run", "--args", "-p hello"])
    assert result.exit_code == 0
    assert "claude_args: ['-p', 'hello']" in result.stdout


def test_multiple_flags_work_together():
    """Multiple flags work together (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--yolo", "--allow-network", "--rebuild", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allow-network: True" in result.stdout
    assert "--rebuild: True" in result.stdout


def test_backend_flag_recognized():
    """--backend flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--backend=podman", "--dry-run"])
    assert result.exit_code == 0
    assert "--backend: podman" in result.stdout


def test_backend_openshift_shows_openshift_options():
    """--backend=openshift shows OpenShift-specific options."""
    result = runner.invoke(app, ["create", "--backend=openshift", "--dry-run"])
    assert result.exit_code == 0
    assert "--backend: openshift" in result.stdout
    assert "--openshift-namespace:" in result.stdout


def test_bare_paude_shows_list():
    """Bare 'paude' command shows session list with helpful hints."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    # Should show either "No sessions found." or the session list header
    assert "No sessions found." in result.stdout or "NAME" in result.stdout
    # When no sessions, should show helpful next steps
    if "No sessions found." in result.stdout:
        assert "paude create" in result.stdout


@patch("paude.backends.PodmanBackend")
@patch("paude.backends.openshift.OpenShiftBackend")
@patch("paude.backends.openshift.OpenShiftConfig")
def test_start_without_session_shows_helpful_error(
    mock_os_config_class: MagicMock,
    mock_os_backend_class: MagicMock,
    mock_podman_class: MagicMock,
):
    """'paude start' without a session shows helpful error with create hint."""
    # Mock both backends to return no sessions
    mock_podman = MagicMock()
    mock_podman.find_session_for_workspace.return_value = None
    mock_podman.list_sessions.return_value = []
    mock_podman_class.return_value = mock_podman

    mock_os_backend = MagicMock()
    mock_os_backend.find_session_for_workspace.return_value = None
    mock_os_backend.list_sessions.return_value = []
    mock_os_backend_class.return_value = mock_os_backend

    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1
    # Should show helpful message with create command (error goes to stderr)
    output = result.stdout + (result.stderr or "")
    assert "No sessions found" in output or "paude create" in output


def test_help_shows_commands():
    """Help shows commands section."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "COMMANDS:" in result.stdout
    assert "create" in result.stdout
    assert "start" in result.stdout
    assert "stop" in result.stdout
    assert "list" in result.stdout
    assert "sync" in result.stdout


def test_stop_help():
    """'stop --help' shows subcommand help, not main help."""
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "stop" in result.stdout.lower()
    assert "Stop a session" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


def test_list_help():
    """'list --help' shows subcommand help."""
    result = runner.invoke(app, ["list", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout.lower()
    assert "List all sessions" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


def test_connect_help():
    """'connect --help' shows subcommand help."""
    result = runner.invoke(app, ["connect", "--help"])
    assert result.exit_code == 0
    assert "connect" in result.stdout.lower()
    assert "Attach to a running session" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


def test_sync_help():
    """'sync --help' shows subcommand help."""
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "sync" in result.stdout.lower()
    assert "Sync files" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


def test_subcommand_runs_without_main_execution():
    """Subcommands run without triggering main execution logic."""
    # This test verifies that subcommands don't trigger podman checks
    # by confirming they complete without the "podman required" error
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Stop a session" in result.stdout
    assert "podman is required" not in result.stdout


# Tests for connect command multi-backend search behavior


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


class TestConnectMultiBackend:
    """Tests for connect command searching multiple backends."""

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_finds_openshift_session_when_podman_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect finds OpenShift running session when podman has none."""
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend.connect_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        assert "Connecting to 'os-session' (openshift)..." in result.output
        mock_os_backend.connect_session.assert_called_once_with("os-session")

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_finds_podman_session_when_openshift_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect finds podman running session when OpenShift has none."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman.connect_session.return_value = 0
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        assert "Connecting to 'podman-session' (podman)..." in result.output
        mock_podman.connect_session.assert_called_once_with("podman-session")

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_shows_multiple_sessions_across_backends(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect shows all sessions when multiple exist across backends."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 1
        assert "Multiple running sessions found" in result.output
        # Verify actionable command syntax is shown
        assert "paude connect podman-session" in result.output
        assert "paude connect os-session" in result.output
        # Verify backend info is shown
        assert "podman" in result.output
        assert "openshift" in result.output

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_no_sessions_shows_error(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect shows error when no running sessions exist."""
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 1
        assert "No running sessions to connect to" in result.output
        # Verify helpful guidance is shown
        assert "paude list" in result.output
        assert "paude start" in result.output

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_prefers_workspace_match_in_podman(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect prefers workspace-matching session in podman."""
        cwd = Path("/my/workspace")

        workspace_session = _make_session(
            "workspace-session", workspace=cwd, backend_type="podman"
        )
        workspace_session.status = "running"
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = workspace_session
        mock_podman.connect_session.return_value = 0
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        assert "Connecting to 'workspace-session' (podman)..." in result.output
        mock_podman.connect_session.assert_called_once_with("workspace-session")
        # OpenShift should not be checked since podman had workspace match
        mock_os_backend.find_session_for_workspace.assert_not_called()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_finds_workspace_match_in_openshift(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect finds workspace-matching session in OpenShift when podman has none."""
        cwd = Path("/my/workspace")

        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        workspace_session = _make_session(
            "os-workspace-session", workspace=cwd, backend_type="openshift"
        )
        workspace_session.status = "running"
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = workspace_session
        mock_os_backend.connect_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        assert "Connecting to 'os-workspace-session' (openshift)..." in result.output
        mock_os_backend.connect_session.assert_called_once_with("os-workspace-session")

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_handles_podman_unavailable(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect works when podman is unavailable."""
        mock_podman_class.side_effect = Exception("podman not found")

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend.connect_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        mock_os_backend.connect_session.assert_called_once_with("os-session")

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_handles_openshift_unavailable(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect works when OpenShift is unavailable."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman.connect_session.return_value = 0
        mock_podman_class.return_value = mock_podman

        mock_os_backend_class.side_effect = Exception("oc not found")

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        mock_podman.connect_session.assert_called_once_with("podman-session")

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_connect_ignores_stopped_sessions(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Connect ignores stopped sessions when searching."""
        stopped_session = _make_session(
            "stopped-session", status="stopped", backend_type="podman"
        )
        running_session = _make_session("running-session", backend_type="openshift")

        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [stopped_session]
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [running_session]
        mock_os_backend.connect_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 0
        mock_os_backend.connect_session.assert_called_once_with("running-session")


class TestStartMultiBackend:
    """Tests for start command searching multiple backends."""

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_start_finds_openshift_session_when_podman_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start finds OpenShift session when podman has none."""
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend.start_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        assert "Starting 'os-session' (openshift)..." in result.output
        mock_os_backend.start_session.assert_called_once()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_start_finds_podman_session_when_openshift_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start finds podman session when OpenShift has none."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman.start_session.return_value = 0
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        assert "Starting 'podman-session' (podman)..." in result.output
        mock_podman.start_session.assert_called_once()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_start_shows_multiple_sessions_across_backends(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start shows all sessions when multiple exist across backends."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 1
        assert "Multiple sessions found" in result.output
        assert "paude start podman-session" in result.output
        assert "paude start os-session" in result.output

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_start_prefers_workspace_match(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start prefers workspace-matching session."""
        workspace_session = _make_session(
            "workspace-session", backend_type="openshift"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = workspace_session
        mock_os_backend.start_session.return_value = 0
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        assert "Starting 'workspace-session' (openshift)..." in result.output
        mock_os_backend.start_session.assert_called_once()


class TestStopMultiBackend:
    """Tests for stop command searching multiple backends."""

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_stop_finds_openshift_session_when_podman_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop finds OpenShift running session when podman has none."""
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = []
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0
        assert "Stopping 'os-session' (openshift)..." in result.output
        assert "Session 'os-session' stopped." in result.output
        mock_os_backend.stop_session.assert_called_once()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_stop_finds_podman_session_when_openshift_empty(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop finds podman running session when OpenShift has none."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0
        assert "Stopping 'podman-session' (podman)..." in result.output
        assert "Session 'podman-session' stopped." in result.output
        mock_podman.stop_session.assert_called_once()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_stop_shows_multiple_running_sessions_across_backends(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop shows all running sessions when multiple exist across backends."""
        podman_session = _make_session("podman-session", backend_type="podman")
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [podman_session]
        mock_podman_class.return_value = mock_podman

        os_session = _make_session("os-session", backend_type="openshift")
        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = [os_session]
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 1
        assert "Multiple running sessions found" in result.output
        assert "paude stop podman-session" in result.output
        assert "paude stop os-session" in result.output

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_stop_prefers_workspace_match(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop prefers workspace-matching running session."""
        workspace_session = _make_session(
            "workspace-session", backend_type="openshift"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = workspace_session
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0
        assert "Stopping 'workspace-session' (openshift)..." in result.output
        assert "Session 'workspace-session' stopped." in result.output
        mock_os_backend.stop_session.assert_called_once()

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_stop_ignores_stopped_sessions(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop only considers running sessions, not stopped ones."""
        stopped_session = _make_session(
            "stopped-session", status="stopped", backend_type="podman"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [stopped_session]
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 1
        assert "No running sessions to stop." in result.output
