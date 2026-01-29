"""Tests for git_remote module."""

from unittest.mock import patch

from paude.git_remote import (
    build_openshift_remote_url,
    build_podman_remote_url,
    enable_ext_protocol,
    get_current_branch,
    git_remote_add,
    git_remote_remove,
    is_ext_protocol_allowed,
    is_git_repository,
    list_paude_remotes,
)


class TestBuildOpenshiftRemoteUrl:
    """Tests for build_openshift_remote_url."""

    def test_basic_url(self) -> None:
        """Build URL without context."""
        url = build_openshift_remote_url(
            pod_name="paude-my-session-0",
            namespace="paude",
        )
        assert url == "ext::oc exec -i paude-my-session-0 -n paude -- %S /pvc/workspace"

    def test_with_context(self) -> None:
        """Build URL with context."""
        url = build_openshift_remote_url(
            pod_name="paude-my-session-0",
            namespace="paude",
            context="my-cluster",
        )
        expected = (
            "ext::oc --context my-cluster exec -i paude-my-session-0 "
            "-n paude -- %S /pvc/workspace"
        )
        assert url == expected

    def test_custom_workspace_path(self) -> None:
        """Build URL with custom workspace path."""
        url = build_openshift_remote_url(
            pod_name="paude-my-session-0",
            namespace="paude",
            workspace_path="/custom/path",
        )
        assert "/custom/path" in url


class TestBuildPodmanRemoteUrl:
    """Tests for build_podman_remote_url."""

    def test_basic_url(self) -> None:
        """Build URL for Podman container."""
        url = build_podman_remote_url(container_name="paude-my-session")
        assert url == "ext::podman exec -i paude-my-session %S /pvc/workspace"

    def test_custom_workspace_path(self) -> None:
        """Build URL with custom workspace path."""
        url = build_podman_remote_url(
            container_name="paude-my-session",
            workspace_path="/custom/path",
        )
        assert url == "ext::podman exec -i paude-my-session %S /custom/path"


class TestGitRemoteAdd:
    """Tests for git_remote_add."""

    @patch("paude.git_remote.subprocess.run")
    def test_successful_add(self, mock_run) -> None:
        """Add a git remote successfully."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = git_remote_add("paude-test", "ext::podman exec -i test %S /workspace")

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "remote", "add", "paude-test", "ext::podman exec -i test %S /workspace"]

    @patch("paude.git_remote.subprocess.run")
    def test_remote_already_exists(self, mock_run) -> None:
        """Handle remote already exists error."""
        mock_run.return_value.returncode = 3
        mock_run.return_value.stderr = "error: remote paude-test already exists"

        result = git_remote_add("paude-test", "ext::podman exec -i test %S /workspace")

        assert result is False


class TestGitRemoteRemove:
    """Tests for git_remote_remove."""

    @patch("paude.git_remote.subprocess.run")
    def test_successful_remove(self, mock_run) -> None:
        """Remove a git remote successfully."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = git_remote_remove("paude-test")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "remote", "remove", "paude-test"]

    @patch("paude.git_remote.subprocess.run")
    def test_remote_not_found(self, mock_run) -> None:
        """Handle remote not found error."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error: No such remote: 'paude-test'"

        result = git_remote_remove("paude-test")

        assert result is False


class TestListPaudeRemotes:
    """Tests for list_paude_remotes."""

    @patch("paude.git_remote.subprocess.run")
    def test_list_remotes(self, mock_run) -> None:
        """List paude git remotes."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = """origin\thttps://github.com/user/repo (fetch)
origin\thttps://github.com/user/repo (push)
paude-my-session\text::podman exec paude-my-session %S /pvc/workspace (fetch)
paude-my-session\text::podman exec paude-my-session %S /pvc/workspace (push)
paude-other\text::oc exec pod -n ns -- %S /pvc/workspace (fetch)
paude-other\text::oc exec pod -n ns -- %S /pvc/workspace (push)
"""

        remotes = list_paude_remotes()

        assert len(remotes) == 2
        assert ("paude-my-session", "ext::podman exec paude-my-session %S /pvc/workspace") in remotes
        assert ("paude-other", "ext::oc exec pod -n ns -- %S /pvc/workspace") in remotes

    @patch("paude.git_remote.subprocess.run")
    def test_no_paude_remotes(self, mock_run) -> None:
        """List returns empty when no paude remotes."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = """origin\thttps://github.com/user/repo (fetch)
origin\thttps://github.com/user/repo (push)
"""

        remotes = list_paude_remotes()

        assert remotes == []

    @patch("paude.git_remote.subprocess.run")
    def test_git_remote_fails(self, mock_run) -> None:
        """Handle git remote command failure."""
        mock_run.return_value.returncode = 1

        remotes = list_paude_remotes()

        assert remotes == []


class TestIsGitRepository:
    """Tests for is_git_repository."""

    @patch("paude.git_remote.subprocess.run")
    def test_is_git_repo(self, mock_run) -> None:
        """Detect git repository."""
        mock_run.return_value.returncode = 0

        result = is_git_repository()

        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_not_git_repo(self, mock_run) -> None:
        """Detect non-git directory."""
        mock_run.return_value.returncode = 128

        result = is_git_repository()

        assert result is False


class TestGetCurrentBranch:
    """Tests for get_current_branch."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_branch_name(self, mock_run) -> None:
        """Return current branch name."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "main\n"

        result = get_current_branch()

        assert result == "main"

    @patch("paude.git_remote.subprocess.run")
    def test_returns_none_on_failure(self, mock_run) -> None:
        """Return None when not on a branch or not in git repo."""
        mock_run.return_value.returncode = 128

        result = get_current_branch()

        assert result is None

    @patch("paude.git_remote.subprocess.run")
    def test_strips_whitespace(self, mock_run) -> None:
        """Strip whitespace from branch name."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "  feature-branch  \n"

        result = get_current_branch()

        assert result == "feature-branch"


class TestIsExtProtocolAllowed:
    """Tests for is_ext_protocol_allowed."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_when_always(self, mock_run) -> None:
        """Return True when protocol.ext.allow is 'always'."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "always\n"

        result = is_ext_protocol_allowed()

        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_when_user(self, mock_run) -> None:
        """Return True when protocol.ext.allow is 'user'."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "user\n"

        result = is_ext_protocol_allowed()

        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_never(self, mock_run) -> None:
        """Return False when protocol.ext.allow is 'never'."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "never\n"

        result = is_ext_protocol_allowed()

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_not_set(self, mock_run) -> None:
        """Return False when protocol.ext.allow is not set."""
        mock_run.return_value.returncode = 1  # Config key not found

        result = is_ext_protocol_allowed()

        assert result is False


class TestEnableExtProtocol:
    """Tests for enable_ext_protocol."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when git config succeeds."""
        mock_run.return_value.returncode = 0

        result = enable_ext_protocol()

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "config", "protocol.ext.allow", "always"]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when git config fails."""
        mock_run.return_value.returncode = 1

        result = enable_ext_protocol()

        assert result is False
