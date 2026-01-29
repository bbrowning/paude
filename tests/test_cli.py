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
    result = runner.invoke(app, ["create", "--yolo", "--allowed-domains", "all", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allowed-domains: unrestricted" in result.stdout


def test_yolo_flag_recognized():
    """--yolo flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--yolo", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout


def test_allowed_domains_default_value():
    """Default --allowed-domains value shows vertexai + pypi."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "--allowed-domains:" in result.stdout
    # Default should expand to vertexai + pypi
    assert "vertexai" in result.stdout or "pypi" in result.stdout


def test_allowed_domains_all_value():
    """--allowed-domains all shows unrestricted."""
    result = runner.invoke(app, ["create", "--allowed-domains", "all", "--dry-run"])
    assert result.exit_code == 0
    assert "--allowed-domains: unrestricted" in result.stdout


def test_allowed_domains_custom_domain():
    """--allowed-domains with custom domain."""
    result = runner.invoke(app, ["create", "--allowed-domains", ".example.com", "--dry-run"])
    assert result.exit_code == 0
    assert ".example.com" in result.stdout


def test_allowed_domains_multiple_values():
    """--allowed-domains can be repeated."""
    result = runner.invoke(app, [
        "create",
        "--allowed-domains", "vertexai",
        "--allowed-domains", ".example.com",
        "--dry-run",
    ])
    assert result.exit_code == 0
    # Should show both
    assert "vertexai" in result.stdout or ".example.com" in result.stdout


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
    result = runner.invoke(app, ["create", "--yolo", "--allowed-domains", "all", "--rebuild", "--dry-run"])
    assert result.exit_code == 0
    assert "--yolo: True" in result.stdout
    assert "--allowed-domains: unrestricted" in result.stdout
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


def test_remote_help():
    """'remote --help' shows subcommand help."""
    result = runner.invoke(app, ["remote", "--help"])
    assert result.exit_code == 0
    assert "remote" in result.stdout.lower()
    assert "git" in result.stdout.lower() or "ACTION" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


class TestRemoteCommand:
    """Tests for paude remote command."""

    @patch("paude.git_remote.list_paude_remotes")
    def test_remote_list_shows_remotes(self, mock_list):
        """remote list shows all paude git remotes."""
        mock_list.return_value = [
            ("paude-my-session", "ext::podman exec paude-my-session %S /pvc/workspace"),
            ("paude-other", "ext::oc exec pod -n ns -- %S /pvc/workspace"),
        ]

        result = runner.invoke(app, ["remote", "list"])

        assert result.exit_code == 0
        assert "paude-my-session" in result.stdout
        assert "paude-other" in result.stdout

    @patch("paude.git_remote.list_paude_remotes")
    def test_remote_list_empty(self, mock_list):
        """remote list shows helpful message when no remotes."""
        mock_list.return_value = []

        result = runner.invoke(app, ["remote", "list"])

        assert result.exit_code == 0
        assert "No paude git remotes found" in result.stdout
        assert "paude remote add" in result.stdout

    @patch("paude.git_remote.is_git_repository")
    def test_remote_add_requires_git_repo(self, mock_is_git):
        """remote add fails if not in git repository."""
        mock_is_git.return_value = False

        result = runner.invoke(app, ["remote", "add", "my-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Not a git repository" in output

    @patch("paude.git_remote.is_git_repository")
    def test_remote_remove_requires_git_repo(self, mock_is_git):
        """remote remove fails if not in git repository."""
        mock_is_git.return_value = False

        result = runner.invoke(app, ["remote", "remove", "my-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Not a git repository" in output

    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.git_remote_remove")
    def test_remote_remove_success(self, mock_remove, mock_is_git):
        """remote remove successfully removes a remote."""
        mock_is_git.return_value = True
        mock_remove.return_value = True

        result = runner.invoke(app, ["remote", "remove", "my-session"])

        assert result.exit_code == 0
        assert "Removed git remote 'paude-my-session'" in result.stdout
        mock_remove.assert_called_once_with("paude-my-session")

    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.git_remote_remove")
    def test_remote_remove_not_found(self, mock_remove, mock_is_git):
        """remote remove fails when remote doesn't exist."""
        mock_is_git.return_value = True
        mock_remove.return_value = False

        result = runner.invoke(app, ["remote", "remove", "nonexistent"])

        assert result.exit_code == 1

    def test_remote_unknown_action(self):
        """remote with unknown action shows error."""
        result = runner.invoke(app, ["remote", "invalid"])

        assert result.exit_code == 1
        # Error goes to stderr, which typer may redirect to stdout
        output = result.stdout + (result.stderr or "")
        assert "Unknown action: invalid" in output
        assert "Valid actions: add, list, remove" in output

    @patch("paude.cli.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    def test_remote_add_fails_when_container_not_running(
        self, mock_running, mock_ext, mock_is_git, mock_find
    ):
        """remote add fails if container is not running."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = False

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "test-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Container not running" in output
        assert "paude start test-session" in output

    @patch("paude.cli.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace_podman")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.git_push_to_remote")
    def test_remote_add_with_push_flag(
        self,
        mock_push,
        mock_branch,
        mock_add,
        mock_init,
        mock_running,
        mock_ext,
        mock_is_git,
        mock_find,
    ):
        """remote add --push adds remote and pushes."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = True
        mock_init.return_value = True
        mock_add.return_value = True
        mock_branch.return_value = "main"
        mock_push.return_value = True

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "--push", "test-session"])

        assert result.exit_code == 0
        output = result.stdout + (result.stderr or "")
        assert "Added git remote" in output
        assert "Pushing main to container" in output
        assert "Push complete" in output
        mock_init.assert_called_once_with("paude-test-session", branch="main")
        mock_push.assert_called_once_with("paude-test-session", "main")

    @patch("paude.cli.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace_podman")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    def test_remote_add_initializes_container_workspace(
        self,
        mock_branch,
        mock_add,
        mock_init,
        mock_running,
        mock_ext,
        mock_is_git,
        mock_find,
    ):
        """remote add initializes git in container before adding remote."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = True
        mock_init.return_value = True
        mock_add.return_value = True
        mock_branch.return_value = "main"

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "test-session"])

        assert result.exit_code == 0
        output = result.stdout + (result.stderr or "")
        assert "Initializing git repository in container" in output
        mock_init.assert_called_once_with("paude-test-session", branch="main")


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

    @patch("paude.backends.PodmanBackend")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_start_includes_stopped_sessions(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start includes stopped sessions (unlike stop which only considers running)."""
        # Create a stopped session - start should still find and start it
        stopped_session = _make_session(
            "stopped-session", status="stopped", backend_type="podman"
        )
        mock_podman = MagicMock()
        mock_podman.find_session_for_workspace.return_value = None
        mock_podman.list_sessions.return_value = [stopped_session]
        mock_podman.start_session.return_value = 0
        mock_podman_class.return_value = mock_podman

        mock_os_backend = MagicMock()
        mock_os_backend.find_session_for_workspace.return_value = None
        mock_os_backend.list_sessions.return_value = []
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(app, ["start"])

        # Start should find the stopped session and start it
        assert result.exit_code == 0
        assert "Starting 'stopped-session' (podman)..." in result.output
        mock_podman.start_session.assert_called_once()


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


class TestDeleteGitRemoteCleanup:
    """Tests for git remote cleanup when deleting sessions."""

    @patch("paude.cli._cleanup_session_git_remote")
    @patch("paude.backends.PodmanBackend")
    def test_delete_removes_git_remote(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete calls git remote cleanup after successful session deletion."""
        mock_podman = MagicMock()
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        mock_cleanup.assert_called_once_with("my-session")

    @patch("paude.cli.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.backends.PodmanBackend")
    def test_delete_works_when_not_in_git_repo(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_subprocess_run: MagicMock,
    ):
        """Delete works when not in a git repository."""
        mock_is_git.return_value = False
        mock_podman = MagicMock()
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should not show "Removed git remote" since not in git repo
        assert "Removed git remote" not in result.output
        # Should not have called git remote remove since not in git repo
        mock_subprocess_run.assert_not_called()

    @patch("paude.cli.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.backends.PodmanBackend")
    def test_delete_works_when_remote_does_not_exist(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete works when git remote doesn't exist."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1, stderr="error: No such remote: 'paude-my-session'"
        )
        mock_podman = MagicMock()
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should not print anything about git remote since it didn't exist
        assert "Removed git remote" not in result.output
        assert "Warning" not in result.output
        # Verify correct command was called
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
        )

    @patch("paude.cli.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.backends.PodmanBackend")
    def test_delete_shows_message_when_remote_removed(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete shows message when git remote is successfully removed."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        assert "Removed git remote 'paude-my-session'." in result.output

    @patch("paude.cli.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.backends.PodmanBackend")
    def test_delete_continues_on_git_remote_failure(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete continues even if git remote removal fails unexpectedly."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1, stderr="fatal: some other error"
        )
        mock_podman = MagicMock()
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        # Session delete should still succeed
        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should show warning about git failure with the error message
        output = result.stdout + (result.stderr or "")
        assert "Warning: Failed to remove git remote: fatal: some other error" in output

    @patch("paude.cli._cleanup_session_git_remote")
    @patch("paude.backends.PodmanBackend")
    def test_delete_does_not_cleanup_git_remote_on_failure(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Git remote cleanup is NOT called when session deletion fails."""
        mock_podman = MagicMock()
        mock_podman.delete_session.side_effect = Exception("Deletion failed")
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 1
        # Cleanup should NOT have been called since deletion failed
        mock_cleanup.assert_not_called()

    @patch("paude.cli._cleanup_session_git_remote")
    @patch("paude.cli.find_session_backend")
    def test_delete_cleans_git_remote_with_auto_detected_backend(
        self,
        mock_find_backend: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete cleans up git remote when backend is auto-detected."""
        mock_backend = MagicMock()
        mock_find_backend.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["delete", "auto-session", "--confirm"])

        assert result.exit_code == 0
        mock_cleanup.assert_called_once_with("auto-session")

    @patch("paude.cli._cleanup_session_git_remote")
    @patch("paude.backends.openshift.OpenShiftBackend")
    @patch("paude.backends.openshift.OpenShiftConfig")
    def test_delete_cleans_git_remote_with_openshift_backend(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete cleans up git remote when using OpenShift backend."""
        mock_os_backend = MagicMock()
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(
            app, ["delete", "os-session", "--confirm", "--backend=openshift"]
        )

        assert result.exit_code == 0
        assert "Session 'os-session' deleted." in result.output
        mock_cleanup.assert_called_once_with("os-session")
