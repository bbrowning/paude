"""Tests for CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from paude.backends import Session
from paude.cli import _parse_copy_path, app

runner = CliRunner()


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--help", id="long-flag"),
        pytest.param("-h", id="short-flag"),
    ],
)
def test_help_shows_help(flag):
    """Help flag shows help and exits 0."""
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "paude - Run AI coding agents" in result.stdout


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--version", id="long-flag"),
        pytest.param("-V", id="short-flag"),
    ],
)
def test_version_shows_version(flag):
    """Version flag shows version and exits 0."""
    from paude import __version__

    result = runner.invoke(app, [flag])
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


def test_dry_run_shows_no_config(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """--dry-run shows 'none' when no config file exists."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "Configuration: none" in result.stdout


def test_dry_run_shows_flag_states():
    """--dry-run shows flag states."""
    result = runner.invoke(
        app, ["create", "--yolo", "--allowed-domains", "all", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "yolo: True" in result.stdout
    assert "allowed-domains: unrestricted" in result.stdout


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        pytest.param("--yolo", "yolo", id="yolo"),
        pytest.param("--rebuild", "rebuild", id="rebuild"),
        pytest.param("--verbose", "verbose", id="verbose"),
    ],
)
def test_flag_recognized(flag, name):
    """Boolean flags are recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", flag, "--dry-run"])
    assert result.exit_code == 0
    assert f"{name}: True" in result.stdout


def test_allowed_domains_default_value():
    """Default --allowed-domains value shows vertexai + python."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "allowed-domains:" in result.stdout
    # Default should expand to vertexai + python
    assert "vertexai" in result.stdout or "python" in result.stdout


def test_allowed_domains_all_value():
    """--allowed-domains all shows unrestricted."""
    result = runner.invoke(app, ["create", "--allowed-domains", "all", "--dry-run"])
    assert result.exit_code == 0
    assert "allowed-domains: unrestricted" in result.stdout


def test_allowed_domains_custom_domain():
    """--allowed-domains with custom domain."""
    result = runner.invoke(
        app, ["create", "--allowed-domains", ".example.com", "--dry-run"]
    )
    assert result.exit_code == 0
    assert ".example.com" in result.stdout


def test_allowed_domains_multiple_values():
    """--allowed-domains can be repeated."""
    result = runner.invoke(
        app,
        [
            "create",
            "--allowed-domains",
            "vertexai",
            "--allowed-domains",
            ".example.com",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    # Should show both
    assert "vertexai" in result.stdout or ".example.com" in result.stdout


def test_help_shows_dry_run_option():
    """--help shows --dry-run option."""
    result = runner.invoke(app, ["--help"])
    assert "--dry-run" in result.stdout


def test_args_option():
    """--args option is parsed and captured in claude_args (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--dry-run", "--args", "-p hello"])
    assert result.exit_code == 0
    assert "args: ['-p', 'hello']" in result.stdout


def test_multiple_flags_work_together():
    """Multiple flags work together (verified via dry-run)."""
    result = runner.invoke(
        app, ["create", "--yolo", "--allowed-domains", "all", "--rebuild", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "yolo: True" in result.stdout
    assert "allowed-domains: unrestricted" in result.stdout
    assert "rebuild: True" in result.stdout


def test_backend_flag_recognized():
    """--backend flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--backend=podman", "--dry-run"])
    assert result.exit_code == 0
    assert "backend: podman" in result.stdout


def test_backend_openshift_shows_openshift_options():
    """--backend=openshift shows OpenShift-specific options."""
    result = runner.invoke(app, ["create", "--backend=openshift", "--dry-run"])
    assert result.exit_code == 0
    assert "backend: openshift" in result.stdout
    assert "openshift-namespace:" in result.stdout


def test_github_domains_in_default_dry_run():
    """GitHub domains appear in dry-run output by default (github is in DEFAULT_ALIASES)."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "github" in result.stdout


def _extract_domains_display(stdout: str) -> str:
    """Extract the allowed-domains value from dry-run output."""
    parts = stdout.split("allowed-domains:")
    assert len(parts) > 1, f"allowed-domains not found in output:\n{stdout}"
    return parts[1].split("\n")[0].strip()


class TestAgentSpecificDomainExpansion:
    """Verify that --agent affects which default domains are expanded."""

    @pytest.fixture(scope="class")
    def claude_dry_run(self):
        result = runner.invoke(app, ["create", "--agent", "claude", "--dry-run"])
        assert result.exit_code == 0
        return result

    @pytest.fixture(scope="class")
    def gemini_dry_run(self):
        result = runner.invoke(app, ["create", "--agent", "gemini", "--dry-run"])
        assert result.exit_code == 0
        return result

    def test_claude_default_includes_claude_alias(self, claude_dry_run):
        """--agent claude default domains include claude alias."""
        assert "claude" in _extract_domains_display(claude_dry_run.stdout)

    def test_claude_default_excludes_gemini_alias(self, claude_dry_run):
        """--agent claude default domains exclude gemini alias."""
        assert "gemini" not in _extract_domains_display(claude_dry_run.stdout)

    def test_gemini_default_includes_gemini_alias(self, gemini_dry_run):
        """--agent gemini default domains include gemini alias."""
        assert "gemini" in _extract_domains_display(gemini_dry_run.stdout)

    def test_gemini_default_includes_nodejs_alias(self, gemini_dry_run):
        """--agent gemini default domains include nodejs alias."""
        assert "nodejs" in _extract_domains_display(gemini_dry_run.stdout)

    def test_gemini_default_excludes_claude_alias(self, gemini_dry_run):
        """--agent gemini default domains exclude claude alias."""
        assert "claude" not in _extract_domains_display(gemini_dry_run.stdout)

    def test_both_agents_include_shared_base_aliases(
        self, claude_dry_run, gemini_dry_run
    ):
        """Both agents include vertexai, python, and github in defaults."""
        for base in ["vertexai", "python", "github"]:
            assert base in claude_dry_run.stdout, f"{base} missing from claude"
            assert base in gemini_dry_run.stdout, f"{base} missing from gemini"

    def test_explicit_domains_override_agent_defaults(self):
        """Explicit --allowed-domains ignores agent-specific defaults."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agent",
                "gemini",
                "--allowed-domains",
                "vertexai",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "gemini" not in _extract_domains_display(result.stdout)


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("start", id="start"),
        pytest.param("connect", id="connect"),
    ],
)
@patch("paude.cli.commands.find_session_backend")
def test_command_accepts_github_token_flag(
    mock_find_session_backend: MagicMock, command
):
    """start/connect accept --github-token flag (session not found is expected)."""
    mock_find_session_backend.return_value = None  # Session not found
    result = runner.invoke(
        app, [command, "test-session", "--github-token", "ghp_test123"]
    )
    assert "No such option" not in result.output
    assert result.exit_code == 1  # Session not found is expected


@pytest.mark.parametrize(
    ("command", "backend_method", "token"),
    [
        pytest.param("start", "start_session", "ghp_test123", id="start"),
        pytest.param("connect", "connect_session", "ghp_test456", id="connect"),
    ],
)
@patch("paude.cli.commands.find_session_backend")
def test_command_passes_github_token_to_backend(
    mock_find_session_backend: MagicMock, command, backend_method, token
):
    """start/connect pass the resolved github_token to the backend."""
    mock_backend = MagicMock()
    getattr(mock_backend, backend_method).return_value = 0
    mock_find_session_backend.return_value = (MagicMock(), mock_backend)

    runner.invoke(app, [command, "test-session", "--github-token", token])

    getattr(mock_backend, backend_method).assert_called_once_with(
        "test-session",
        github_token=token,  # noqa: S106
    )


@patch("paude.cli.commands.find_session_backend")
def test_start_reads_paude_github_token_env(
    mock_find_session_backend: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    """paude start reads PAUDE_GITHUB_TOKEN env var when --github-token not provided."""
    monkeypatch.setenv("PAUDE_GITHUB_TOKEN", "ghp_from_env")
    mock_backend = MagicMock()
    mock_backend.start_session.return_value = 0
    mock_find_session_backend.return_value = (MagicMock(), mock_backend)

    runner.invoke(app, ["start", "test-session"])

    mock_backend.start_session.assert_called_once_with(
        "test-session",
        github_token="ghp_from_env",  # noqa: S106
    )


def test_create_does_not_accept_github_token():
    """paude create does NOT accept --github-token (token belongs on start/connect)."""
    result = runner.invoke(app, ["create", "--dry-run", "--github-token", "ghp_test"])
    assert result.exit_code != 0
    assert "No such option" in result.output or "Error" in result.output


def test_bare_paude_shows_list():
    """Bare 'paude' command shows session list with helpful hints."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    # Should show either "No sessions found." or the session list header
    assert "No sessions found." in result.stdout or "NAME" in result.stdout
    # When no sessions, should show helpful next steps
    if "No sessions found." in result.stdout:
        assert "paude create" in result.stdout


@patch("paude.session_discovery.PodmanBackend")
@patch("paude.session_discovery.OpenShiftBackend")
@patch("paude.session_discovery.OpenShiftConfig")
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


@pytest.mark.parametrize(
    ("command", "description"),
    [
        pytest.param("stop", "Stop a session", id="stop"),
        pytest.param("list", "List all sessions", id="list"),
        pytest.param("connect", "Attach to a running session", id="connect"),
    ],
)
def test_subcommand_help(command, description):
    """Subcommand --help shows its own help, not main help."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert command in result.stdout.lower()
    assert description in result.stdout
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

    @pytest.mark.parametrize(
        "action",
        [
            pytest.param("add", id="add"),
            pytest.param("remove", id="remove"),
            pytest.param("cleanup", id="cleanup"),
        ],
    )
    @patch("paude.git_remote.is_git_repository")
    def test_remote_action_requires_git_repo(self, mock_is_git, action):
        """remote add/remove/cleanup fails if not in git repository."""
        mock_is_git.return_value = False

        result = runner.invoke(app, ["remote", action, "my-session"])

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
        assert "Valid actions: add, list, remove, cleanup" in output

    @patch("paude.cli.remote.find_session_backend")
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

    @patch("paude.cli.remote.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace_podman")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.git_push_to_remote")
    @patch("paude.git_remote.set_base_ref_in_container_podman")
    def test_remote_add_with_push_flag(
        self,
        mock_set_base_ref,
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
        mock_set_base_ref.return_value = True

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

    @patch("paude.cli.remote.find_session_backend")
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

    @pytest.fixture(autouse=True)
    def _clear_github_token(self, monkeypatch):
        monkeypatch.delenv("PAUDE_GITHUB_TOKEN", raising=False)

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_os_backend.connect_session.assert_called_once_with(
            "os-session", github_token=None
        )

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_podman.connect_session.assert_called_once_with(
            "podman-session", github_token=None
        )

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_podman.connect_session.assert_called_once_with(
            "workspace-session", github_token=None
        )
        # OpenShift should not be checked since podman had workspace match
        mock_os_backend.find_session_for_workspace.assert_not_called()

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_os_backend.connect_session.assert_called_once_with(
            "os-workspace-session", github_token=None
        )

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_os_backend.connect_session.assert_called_once_with(
            "os-session", github_token=None
        )

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_podman.connect_session.assert_called_once_with(
            "podman-session", github_token=None
        )

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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
        mock_os_backend.connect_session.assert_called_once_with(
            "running-session", github_token=None
        )


class TestStartMultiBackend:
    """Tests for start command searching multiple backends."""

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
    def test_start_prefers_workspace_match(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Start prefers workspace-matching session."""
        workspace_session = _make_session("workspace-session", backend_type="openshift")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
    def test_stop_prefers_workspace_match(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_podman_class: MagicMock,
    ):
        """Stop prefers workspace-matching running session."""
        workspace_session = _make_session("workspace-session", backend_type="openshift")
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

    @patch("paude.session_discovery.PodmanBackend")
    @patch("paude.session_discovery.OpenShiftBackend")
    @patch("paude.session_discovery.OpenShiftConfig")
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

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_removes_git_remote(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete calls git remote cleanup after successful session deletion."""
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        mock_cleanup.assert_called_once_with("my-session", Path("/some/project"))

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_works_when_not_in_git_repo(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_subprocess_run: MagicMock,
    ):
        """Delete works when not in a git repository."""
        mock_is_git.return_value = False
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
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

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
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
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should not print anything about git remote since it didn't exist
        assert "Removed git remote" not in result.output
        assert "Warning" not in result.output
        # Verify correct command was called (cwd=None since workspace is None)
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=None,
        )

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
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
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        assert "Removed git remote 'paude-my-session'." in result.output

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
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
        mock_podman.get_session.return_value = None
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

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_does_not_cleanup_git_remote_on_failure(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Git remote cleanup is NOT called when session deletion fails."""
        mock_podman = MagicMock()
        mock_podman.delete_session.side_effect = Exception("Deletion failed")
        mock_podman.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 1
        # Cleanup should NOT have been called since deletion failed
        mock_cleanup.assert_not_called()

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.commands.find_session_backend")
    def test_delete_cleans_git_remote_with_auto_detected_backend(
        self,
        mock_find_backend: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete cleans up git remote when backend is auto-detected."""
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_find_backend.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["delete", "auto-session", "--confirm"])

        assert result.exit_code == 0
        mock_cleanup.assert_called_once_with("auto-session", Path("/some/project"))

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.OpenShiftBackend")
    @patch("paude.cli.helpers.OpenShiftConfig")
    def test_delete_cleans_git_remote_with_openshift_backend(
        self,
        mock_os_config_class: MagicMock,
        mock_os_backend_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete cleans up git remote when using OpenShift backend."""
        mock_os_backend = MagicMock()
        mock_os_backend.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_os_backend_class.return_value = mock_os_backend

        result = runner.invoke(
            app, ["delete", "os-session", "--confirm", "--backend=openshift"]
        )

        assert result.exit_code == 0
        assert "Session 'os-session' deleted." in result.output
        mock_cleanup.assert_called_once_with("os-session", Path("/some/project"))


class TestDeleteUsesWorkspacePath:
    """Tests for delete using stored workspace path for git remote cleanup."""

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_cleans_remote_from_workspace_dir(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ):
        """Delete removes git remote from stored workspace directory."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(workspace=tmp_path)
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Removed git remote 'paude-my-session'." in result.output
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_falls_back_to_cwd_when_workspace_not_git(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ):
        """Delete falls back to current dir when workspace is not a git repo."""
        # Workspace is not a git repo, current dir is
        mock_is_git.side_effect = lambda cwd=None: cwd is None
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(workspace=tmp_path)
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=None,
        )

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_passes_none_workspace_when_session_not_found(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete passes None workspace when get_session returns None."""
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        mock_cleanup.assert_called_once_with("my-session", None)


class TestRemoteCleanup:
    """Tests for paude remote cleanup command."""

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_removes_orphaned_remotes(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup removes remotes for sessions that no longer exist."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-active", "ext::podman exec paude-active %S /pvc/workspace"),
            ("paude-orphan", "ext::podman exec paude-orphan %S /pvc/workspace"),
        ]
        active_session = MagicMock()
        active_session.name = "active"
        mock_collect.return_value = [(active_session, MagicMock())]

        with patch("paude.git_remote.git_remote_remove", return_value=True) as mock_rm:
            result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        mock_rm.assert_called_once_with("paude-orphan")
        assert "Removed orphaned remote 'paude-orphan'" in result.stdout
        assert "Removed 1 orphaned remote(s)." in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_no_orphans(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup reports when no orphaned remotes found."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-active", "ext::podman exec paude-active %S /pvc/workspace"),
        ]
        active_session = MagicMock()
        active_session.name = "active"
        mock_collect.return_value = [(active_session, MagicMock())]

        result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert "No orphaned remotes found." in result.stdout

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_no_remotes(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
    ):
        """Cleanup reports when no paude remotes exist."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = []

        result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert "No paude git remotes found." in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_removes_multiple_orphans(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup removes multiple orphaned remotes."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-gone1", "ext::podman exec paude-gone1 %S /pvc/workspace"),
            ("paude-gone2", "ext::podman exec paude-gone2 %S /pvc/workspace"),
        ]
        mock_collect.return_value = []

        with patch("paude.git_remote.git_remote_remove", return_value=True) as mock_rm:
            result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert mock_rm.call_count == 2
        assert "Removed 2 orphaned remote(s)." in result.stdout


class TestParseCopyPath:
    """Tests for _parse_copy_path helper."""

    @pytest.mark.parametrize(
        ("input_path", "expected"),
        [
            pytest.param(
                "/absolute/path", (None, "/absolute/path"), id="absolute-local"
            ),
            pytest.param(
                "./relative/path", (None, "./relative/path"), id="relative-local"
            ),
            pytest.param("file.txt", (None, "file.txt"), id="bare-filename"),
            pytest.param(
                "../parent/file.txt", (None, "../parent/file.txt"), id="parent-relative"
            ),
            pytest.param(
                "my-session:file.txt",
                ("my-session", "file.txt"),
                id="session-with-path",
            ),
            pytest.param(
                "my-session:/abs/path",
                ("my-session", "/abs/path"),
                id="session-absolute",
            ),
            pytest.param(":file.txt", ("", "file.txt"), id="auto-detect-session"),
        ],
    )
    def test_parse_copy_path(self, input_path, expected):
        """_parse_copy_path correctly parses various path formats."""
        assert _parse_copy_path(input_path) == expected


class TestCpCommand:
    """Tests for paude cp command."""

    def test_cp_no_remote_path_errors(self):
        """Both paths local should error."""
        result = runner.invoke(app, ["cp", "./file.txt", "./dest.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "One of SRC or DEST must be a remote path" in output

    def test_cp_both_remote_errors(self):
        """Both paths remote should error."""
        result = runner.invoke(app, ["cp", "sess1:file.txt", "sess2:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Only one of SRC or DEST can be a remote path" in output

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_to_session_calls_copy_to(self, mock_find):
        """cp local -> session calls copy_to_session."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./file.txt", "/pvc/workspace/file.txt"
        )

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_from_session_calls_copy_from(self, mock_find):
        """cp session -> local calls copy_from_session."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "my-session:output.log", "./"])

        assert result.exit_code == 0
        mock_backend.copy_from_session.assert_called_once_with(
            "my-session", "/pvc/workspace/output.log", "./"
        )

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_relative_remote_path_resolved(self, mock_find):
        """Relative remote paths get /pvc/workspace/ prefix."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./local", "my-session:subdir/file"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./local", "/pvc/workspace/subdir/file"
        )

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_absolute_remote_path_preserved(self, mock_find):
        """Absolute remote paths are used as-is."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./local", "my-session:/tmp/file"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./local", "/tmp/file"
        )

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_session_not_found(self, mock_find):
        """Error when session doesn't exist."""
        mock_find.return_value = None

        result = runner.invoke(app, ["cp", "./file.txt", "nonexistent:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "not found" in output

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_copy_failure_shows_error(self, mock_find):
        """Backend raises, CLI shows error."""
        mock_backend = MagicMock()
        mock_backend.copy_to_session.side_effect = Exception("copy failed")
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "copy failed" in output

    @patch("paude.cli.commands.find_session_backend")
    def test_cp_session_not_running_shows_error(self, mock_find):
        """ValueError from backend shows error."""
        mock_backend = MagicMock()
        mock_backend.copy_to_session.side_effect = ValueError(
            "Session 'my-session' is not running."
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "not running" in output

    def test_cp_help(self):
        """'cp --help' shows subcommand help."""
        result = runner.invoke(app, ["cp", "--help"])

        assert result.exit_code == 0
        assert "Copy files between local and a session" in result.stdout

    def test_help_shows_cp_command(self):
        """Main help shows cp command."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "cp" in result.stdout


class TestCreateGitEnvVar:
    """Tests for PAUDE_WAIT_FOR_GIT env var on OpenShift create --git."""

    @pytest.mark.parametrize(
        ("extra_flags", "expect_env_var"),
        [
            (["--git"], True),
            ([], False),
        ],
        ids=["with-git", "without-git"],
    )
    @patch("paude.cli.remote._setup_git_after_create")
    @patch("paude.cli.create.OpenShiftBackend")
    @patch("paude.cli.create.OpenShiftConfig")
    @patch("paude.config.detect_config", return_value=None)
    @patch("paude.environment.build_environment")
    def test_openshift_create_git_wait_env(
        self,
        mock_build_env,
        mock_detect_config,
        mock_os_config_class,
        mock_os_backend_class,
        mock_git_setup,
        extra_flags,
        expect_env_var,
    ):
        """PAUDE_WAIT_FOR_GIT is set only when --git is used with OpenShift."""
        mock_build_env.return_value = {}
        mock_backend = MagicMock()
        mock_backend.namespace = "test-ns"
        mock_backend.ensure_image_via_build.return_value = "test-image:latest"
        mock_backend.ensure_proxy_image_via_build.return_value = None
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_backend.create_session.return_value = mock_session
        mock_os_backend_class.return_value = mock_backend

        runner.invoke(
            app,
            ["create", "--backend", "openshift", *extra_flags, "test-session"],
        )

        mock_backend.create_session.assert_called_once()
        session_config = mock_backend.create_session.call_args[0][0]
        if expect_env_var:
            assert session_config.env.get("PAUDE_WAIT_FOR_GIT") == "1"
        else:
            assert "PAUDE_WAIT_FOR_GIT" not in session_config.env


# ---------------------------------------------------------------------------
# blocked-domains subcommand
# ---------------------------------------------------------------------------


class TestBlockedDomainsCLI:
    """Tests for the blocked-domains CLI subcommand."""

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_unrestricted_network_message(self, mock_resolve: MagicMock) -> None:
        """Shows unrestricted message when no proxy."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = None
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "unrestricted network" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_no_blocked_domains_message(self, mock_resolve: MagicMock) -> None:
        """Shows no-blocked message when log is empty."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = ""
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "No blocked domains" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_raw_output(self, mock_resolve: MagicMock) -> None:
        """--raw dumps raw log content."""
        log = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = log
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session", "--raw"])
        assert result.exit_code == 0
        assert "evil.com:443" in result.stdout
        assert "BLOCKED" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_parsed_summary_output(self, mock_resolve: MagicMock) -> None:
        """Default output shows parsed summary."""
        log = (
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:01:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:02:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT other.com:443 BLOCKED\n"
        )
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = log
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "evil.com" in result.stdout
        assert "other.com" in result.stdout
        assert "2 unique domain(s) blocked (3 total requests)" in result.stdout
        assert "paude allowed-domains my-session --add" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_session_not_found_error(self, mock_resolve: MagicMock) -> None:
        """Shows error when session not found."""
        from paude.backends.podman import SessionNotFoundError

        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.side_effect = SessionNotFoundError(
            "Session 'nope' not found"
        )
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "nope"])
        assert result.exit_code == 1

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_proxy_not_running_error(self, mock_resolve: MagicMock) -> None:
        """Shows error when proxy not running."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.side_effect = ValueError(
            "Proxy for session 'x' is not running."
        )
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "x"])
        assert result.exit_code == 1


def test_help_includes_blocked_domains() -> None:
    """Help output includes blocked-domains command."""
    result = runner.invoke(app, ["--help"])
    assert "blocked-domains" in result.stdout


class TestDetectDevScriptDir:
    """Tests for _detect_dev_script_dir()."""

    def test_returns_project_root_in_src_layout(self, tmp_path: Path) -> None:
        """Returns project root when containers/paude/Dockerfile exists (src layout)."""
        from paude.cli.helpers import _detect_dev_script_dir

        (tmp_path / "containers" / "paude").mkdir(parents=True)
        (tmp_path / "containers" / "paude" / "Dockerfile").touch()

        # Simulate src layout: project_root/src/paude/cli/helpers.py (4 levels)
        fake_file = tmp_path / "src" / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result == tmp_path

    def test_returns_project_root_in_flat_layout(self, tmp_path: Path) -> None:
        """Returns project root when containers/paude/Dockerfile exists (flat layout)."""
        from paude.cli.helpers import _detect_dev_script_dir

        (tmp_path / "containers" / "paude").mkdir(parents=True)
        (tmp_path / "containers" / "paude" / "Dockerfile").touch()

        # Simulate flat layout: project_root/paude/cli/helpers.py (3 levels)
        fake_file = tmp_path / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result == tmp_path

    def test_returns_none_when_no_dockerfile(self, tmp_path: Path) -> None:
        """Returns None when no containers/paude/Dockerfile found."""
        from paude.cli.helpers import _detect_dev_script_dir

        # No Dockerfile anywhere
        fake_file = tmp_path / "src" / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result is None
