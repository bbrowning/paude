"""Tests for git_remote module."""

from pathlib import Path
from unittest.mock import patch

from paude.git_remote import (
    _build_clone_from_origin_cmd,
    _build_openshift_exec_cmd,
    _build_podman_exec_cmd,
    _build_set_origin_cmd,
    _build_workspace_init_cmd,
    _exec_in_container,
    build_openshift_remote_url,
    build_podman_remote_url,
    clone_from_origin_openshift,
    clone_from_origin_podman,
    enable_ext_protocol,
    get_branch_remote_url,
    get_current_branch,
    git_diff_stat,
    git_fetch_from_remote,
    git_push_tags_to_remote,
    git_remote_add,
    git_remote_remove,
    is_ext_protocol_allowed,
    is_git_repository,
    list_paude_remotes,
    resolve_origin_cmd,
    set_origin_in_container_openshift,
    set_origin_in_container_podman,
    setup_precommit_in_container_openshift,
    setup_precommit_in_container_podman,
    ssh_url_to_https,
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
        assert call_args == [
            "git",
            "remote",
            "add",
            "paude-test",
            "ext::podman exec -i test %S /workspace",
        ]

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
        assert (
            "paude-my-session",
            "ext::podman exec paude-my-session %S /pvc/workspace",
        ) in remotes
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


class TestInitializeContainerWorkspacePodman:
    """Tests for initialize_container_workspace_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when git init succeeds."""
        from paude.git_remote import initialize_container_workspace_podman

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = initialize_container_workspace_podman("paude-test")

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when git init fails."""
        from paude.git_remote import initialize_container_workspace_podman

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        result = initialize_container_workspace_podman("paude-test")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_uses_branch_name(self, mock_run) -> None:
        """Use specified branch name in git init."""
        from paude.git_remote import initialize_container_workspace_podman

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = initialize_container_workspace_podman("paude-test", branch="develop")

        assert result is True
        call_args = mock_run.call_args[0][0]
        # Find the bash -c command argument
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "git init -b develop" in bash_cmd


class TestInitializeContainerWorkspaceOpenshift:
    """Tests for initialize_container_workspace_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when git init succeeds."""
        from paude.git_remote import initialize_container_workspace_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = initialize_container_workspace_openshift("pod-0", "namespace")

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "oc" in call_args
        assert "pod-0" in call_args
        assert "-n" in call_args
        assert "namespace" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        from paude.git_remote import initialize_container_workspace_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = initialize_container_workspace_openshift(
            "pod-0", "ns", context="my-ctx"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_uses_branch_name(self, mock_run) -> None:
        """Use specified branch name in git init."""
        from paude.git_remote import initialize_container_workspace_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = initialize_container_workspace_openshift(
            "pod-0", "ns", branch="feature-branch"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        # Find the bash -c command argument
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "git init -b feature-branch" in bash_cmd


class TestIsContainerRunningPodman:
    """Tests for is_container_running_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_when_running(self, mock_run) -> None:
        """Return True when container is running."""
        from paude.git_remote import is_container_running_podman

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "true\n"

        result = is_container_running_podman("paude-test")

        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_not_running(self, mock_run) -> None:
        """Return False when container is not running."""
        from paude.git_remote import is_container_running_podman

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "false\n"

        result = is_container_running_podman("paude-test")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_not_found(self, mock_run) -> None:
        """Return False when container doesn't exist."""
        from paude.git_remote import is_container_running_podman

        mock_run.return_value.returncode = 125

        result = is_container_running_podman("paude-test")

        assert result is False


class TestIsPodRunningOpenshift:
    """Tests for is_pod_running_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_when_running(self, mock_run) -> None:
        """Return True when pod is running."""
        from paude.git_remote import is_pod_running_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Running\n"

        result = is_pod_running_openshift("pod-0", "namespace")

        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_not_running(self, mock_run) -> None:
        """Return False when pod is not running."""
        from paude.git_remote import is_pod_running_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Pending\n"

        result = is_pod_running_openshift("pod-0", "namespace")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_when_not_found(self, mock_run) -> None:
        """Return False when pod doesn't exist."""
        from paude.git_remote import is_pod_running_openshift

        mock_run.return_value.returncode = 1

        result = is_pod_running_openshift("pod-0", "namespace")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        from paude.git_remote import is_pod_running_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Running"

        result = is_pod_running_openshift("pod-0", "ns", context="my-ctx")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args


class TestSetBaseRefInContainerPodman:
    """Tests for set_base_ref_in_container_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting base ref succeeds."""
        from paude.git_remote import set_base_ref_in_container_podman

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_base_ref_in_container_podman("paude-test")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "update-ref refs/paude/base HEAD" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting base ref fails."""
        from paude.git_remote import set_base_ref_in_container_podman

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        result = set_base_ref_in_container_podman("paude-test")

        assert result is False


class TestSetBaseRefInContainerOpenshift:
    """Tests for set_base_ref_in_container_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting base ref succeeds."""
        from paude.git_remote import set_base_ref_in_container_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_base_ref_in_container_openshift("pod-0", "namespace")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "oc" in call_args
        assert "pod-0" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "update-ref refs/paude/base HEAD" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        from paude.git_remote import set_base_ref_in_container_openshift

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_base_ref_in_container_openshift("pod-0", "ns", context="my-ctx")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting base ref fails."""
        from paude.git_remote import set_base_ref_in_container_openshift

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        result = set_base_ref_in_container_openshift("pod-0", "namespace")

        assert result is False


class TestGitPushToRemote:
    """Tests for git_push_to_remote."""

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run, mock_branch) -> None:
        """Return True when push succeeds."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0

        result = git_push_to_remote("paude-test")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "push", "paude-test", "main"]

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_uses_specified_branch(self, mock_run, mock_branch) -> None:
        """Use specified branch instead of current."""
        from paude.git_remote import git_push_to_remote

        mock_run.return_value.returncode = 0

        result = git_push_to_remote("paude-test", "feature-branch")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "push", "paude-test", "feature-branch"]

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run, mock_branch) -> None:
        """Return False when push fails."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 1

        result = git_push_to_remote("paude-test")

        assert result is False


class TestSshUrlToHttps:
    """Tests for ssh_url_to_https."""

    def test_converts_git_at_format(self) -> None:
        """Convert git@host:user/repo.git to HTTPS."""
        result = ssh_url_to_https("git@github.com:user/repo.git")
        assert result == "https://github.com/user/repo.git"

    def test_converts_ssh_protocol_format(self) -> None:
        """Convert ssh://git@host/user/repo.git to HTTPS."""
        result = ssh_url_to_https("ssh://git@github.com/user/repo.git")
        assert result == "https://github.com/user/repo.git"

    def test_preserves_https_url(self) -> None:
        """Return HTTPS URLs unchanged."""
        url = "https://github.com/user/repo.git"
        assert ssh_url_to_https(url) == url

    def test_preserves_http_url(self) -> None:
        """Return HTTP URLs unchanged."""
        url = "http://github.com/user/repo.git"
        assert ssh_url_to_https(url) == url

    def test_converts_gitlab_ssh(self) -> None:
        """Convert GitLab SSH URLs."""
        result = ssh_url_to_https("git@gitlab.com:group/project.git")
        assert result == "https://gitlab.com/group/project.git"

    def test_converts_nested_path(self) -> None:
        """Convert SSH URL with nested group path."""
        result = ssh_url_to_https("git@gitlab.com:group/subgroup/repo.git")
        assert result == "https://gitlab.com/group/subgroup/repo.git"


class TestGitPushTagsToRemote:
    """Tests for git_push_tags_to_remote."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when push tags succeeds."""
        mock_run.return_value.returncode = 0

        result = git_push_tags_to_remote("paude-test")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "push", "paude-test", "--tags"]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when push tags fails."""
        mock_run.return_value.returncode = 1

        result = git_push_tags_to_remote("paude-test")

        assert result is False


class TestSetOriginInContainerPodman:
    """Tests for set_origin_in_container_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting origin succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_origin_in_container_podman(
            "paude-test", "https://github.com/user/repo"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting origin fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        result = set_origin_in_container_podman(
            "paude-test", "https://github.com/user/repo"
        )

        assert result is False


class TestSetOriginInContainerOpenshift:
    """Tests for set_origin_in_container_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting origin succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_origin_in_container_openshift(
            "pod-0", "namespace", "https://github.com/user/repo"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "oc" in call_args
        assert "pod-0" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = set_origin_in_container_openshift(
            "pod-0",
            "ns",
            "https://github.com/user/repo",
            context="my-ctx",
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting origin fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        result = set_origin_in_container_openshift(
            "pod-0", "ns", "https://github.com/user/repo"
        )

        assert result is False


class TestSetupPrecommitInContainerPodman:
    """Tests for setup_precommit_in_container_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when pre-commit install succeeds."""
        mock_run.return_value.returncode = 0

        result = setup_precommit_in_container_podman("paude-test")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_runs_precommit_install(self, mock_run) -> None:
        """Run pre-commit install command in container."""
        mock_run.return_value.returncode = 0

        setup_precommit_in_container_podman("paude-test")

        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "pre-commit install" in bash_cmd
        assert ".pre-commit-config.yaml" in bash_cmd

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when command fails."""
        mock_run.return_value.returncode = 1

        result = setup_precommit_in_container_podman("paude-test")

        assert result is False


class TestSetupPrecommitInContainerOpenshift:
    """Tests for setup_precommit_in_container_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when pre-commit install succeeds."""
        mock_run.return_value.returncode = 0

        result = setup_precommit_in_container_openshift("pod-0", "namespace")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "oc" in call_args
        assert "pod-0" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        mock_run.return_value.returncode = 0

        result = setup_precommit_in_container_openshift("pod-0", "ns", context="my-ctx")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_runs_precommit_install(self, mock_run) -> None:
        """Run pre-commit install command in pod."""
        mock_run.return_value.returncode = 0

        setup_precommit_in_container_openshift("pod-0", "ns")

        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "pre-commit install" in bash_cmd
        assert ".pre-commit-config.yaml" in bash_cmd

    @patch("paude.git_remote.subprocess.run")
    def test_sets_home_for_arbitrary_uid(self, mock_run) -> None:
        """Set HOME explicitly for OpenShift arbitrary UID compatibility."""
        mock_run.return_value.returncode = 0

        setup_precommit_in_container_openshift("pod-0", "ns")

        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        # Must handle both empty HOME and HOME="/" (OpenShift arbitrary UID)
        assert '"$HOME" == "/"' in bash_cmd
        assert "export HOME=" in bash_cmd

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when command fails."""
        mock_run.return_value.returncode = 1

        result = setup_precommit_in_container_openshift("pod-0", "namespace")

        assert result is False


class TestBuildPodmanExecCmd:
    """Tests for _build_podman_exec_cmd."""

    def test_builds_correct_command(self) -> None:
        """Build correct podman exec command."""
        result = _build_podman_exec_cmd("my-container", "echo hello")
        assert result == ["podman", "exec", "my-container", "bash", "-c", "echo hello"]


class TestBuildOpenshiftExecCmd:
    """Tests for _build_openshift_exec_cmd."""

    def test_builds_correct_command_without_context(self) -> None:
        """Build oc exec command without context."""
        result = _build_openshift_exec_cmd("pod-0", "ns", None, "echo hello")
        assert result == [
            "oc",
            "exec",
            "pod-0",
            "-n",
            "ns",
            "--",
            "bash",
            "-c",
            "echo hello",
        ]

    def test_builds_correct_command_with_context(self) -> None:
        """Build oc exec command with context."""
        result = _build_openshift_exec_cmd("pod-0", "ns", "my-ctx", "echo hello")
        assert result == [
            "oc",
            "--context",
            "my-ctx",
            "exec",
            "pod-0",
            "-n",
            "ns",
            "--",
            "bash",
            "-c",
            "echo hello",
        ]


class TestExecInContainer:
    """Tests for _exec_in_container."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when command succeeds."""
        mock_run.return_value.returncode = 0
        result = _exec_in_container(["podman", "exec", "c", "bash", "-c", "true"])
        assert result is True

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when command fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"
        result = _exec_in_container(["podman", "exec", "c", "bash", "-c", "false"])
        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_prints_error_msg_on_failure(self, mock_run, capsys) -> None:
        """Print error message on failure when provided."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "some error"
        _exec_in_container(["cmd"], error_msg="Init failed")
        captured = capsys.readouterr()
        assert "Init failed" in captured.err
        assert "some error" in captured.err


class TestBashCommandBuilders:
    """Tests for bash command builder helpers."""

    def test_build_workspace_init_cmd(self) -> None:
        """Build workspace init command with branch."""
        cmd = _build_workspace_init_cmd("main")
        assert "git init -b main" in cmd
        assert "receive.denyCurrentBranch updateInstead" in cmd
        assert "/pvc/workspace" in cmd

    def test_build_set_origin_cmd(self) -> None:
        """Build set origin command."""
        cmd = _build_set_origin_cmd("https://github.com/user/repo")
        assert "remote add origin" in cmd
        assert "remote set-url origin" in cmd
        assert "https://github.com/user/repo" in cmd

    def test_build_set_origin_cmd_quotes_url(self) -> None:
        """Quote URLs with special characters."""
        cmd = _build_set_origin_cmd("https://example.com/path with spaces")
        assert "'" in cmd or "\\" in cmd


class TestGitFetchFromRemote:
    """Tests for git_fetch_from_remote."""

    @patch("paude.git_remote.subprocess.run")
    def test_fetch_success(self, mock_run) -> None:
        """Return True on successful fetch."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = git_fetch_from_remote("paude-my-session")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "fetch", "paude-my-session"]

    @patch("paude.git_remote.subprocess.run")
    def test_fetch_with_cwd(self, mock_run) -> None:
        """Passes cwd to subprocess."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        git_fetch_from_remote("paude-test", cwd=Path("/tmp/workspace"))

        assert mock_run.call_args[1]["cwd"] == Path("/tmp/workspace")

    @patch("paude.git_remote.subprocess.run")
    def test_fetch_failure(self, mock_run) -> None:
        """Return False on failed fetch."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "fatal: error"

        result = git_fetch_from_remote("bad-remote")

        assert result is False


class TestGitDiffStat:
    """Tests for git_diff_stat."""

    @patch("paude.git_remote.subprocess.run")
    def test_diff_stat_success(self, mock_run) -> None:
        """Return diff stat output on success."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = " 2 files changed, 10 insertions(+)\n"

        result = git_diff_stat("main", "feature")

        assert "2 files changed" in result
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "diff", "--stat", "main...feature"]

    @patch("paude.git_remote.subprocess.run")
    def test_diff_stat_with_cwd(self, mock_run) -> None:
        """Passes cwd to subprocess."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""

        git_diff_stat("main", "feature", cwd=Path("/tmp/workspace"))

        assert mock_run.call_args[1]["cwd"] == Path("/tmp/workspace")

    @patch("paude.git_remote.subprocess.run")
    def test_diff_stat_failure(self, mock_run) -> None:
        """Return empty string on failure."""
        mock_run.return_value.returncode = 1

        result = git_diff_stat("main", "nonexistent")

        assert result == ""


class TestGetBranchRemoteUrl:
    """Tests for get_branch_remote_url."""

    @patch("paude.git_remote.subprocess.run")
    def test_branch_tracks_non_origin_remote(self, mock_run) -> None:
        """Return upstream URL when branch tracks upstream."""
        mock_run.side_effect = [
            # get_current_branch
            type("Result", (), {"returncode": 0, "stdout": "main\n"})(),
            # branch.main.remote -> upstream
            type("Result", (), {"returncode": 0, "stdout": "upstream\n"})(),
            # remote.upstream.url
            type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "https://github.com/vllm-project/vllm.git\n",
                },
            )(),
        ]

        result = get_branch_remote_url()

        assert result == "https://github.com/vllm-project/vllm.git"
        # Verify it looked up upstream's URL, not origin's
        assert mock_run.call_args_list[2][0][0] == [
            "git",
            "config",
            "--get",
            "remote.upstream.url",
        ]

    @patch("paude.git_remote.subprocess.run")
    def test_branch_tracks_origin(self, mock_run) -> None:
        """Return origin URL when branch tracks origin."""
        mock_run.side_effect = [
            # branch.main.remote -> origin
            type("Result", (), {"returncode": 0, "stdout": "origin\n"})(),
            # remote.origin.url
            type(
                "Result",
                (),
                {"returncode": 0, "stdout": "https://github.com/user/repo.git\n"},
            )(),
        ]

        result = get_branch_remote_url("main")

        assert result == "https://github.com/user/repo.git"

    @patch("paude.git_remote.subprocess.run")
    def test_no_tracking_remote_falls_back_to_origin(self, mock_run) -> None:
        """Fall back to origin when no tracking remote is configured."""
        mock_run.side_effect = [
            # branch.feature.remote -> not set
            type("Result", (), {"returncode": 1, "stdout": ""})(),
            # remote.origin.url
            type(
                "Result",
                (),
                {"returncode": 0, "stdout": "https://github.com/user/repo.git\n"},
            )(),
        ]

        result = get_branch_remote_url("feature")

        assert result == "https://github.com/user/repo.git"
        assert mock_run.call_args_list[1][0][0] == [
            "git",
            "config",
            "--get",
            "remote.origin.url",
        ]

    @patch("paude.git_remote.subprocess.run")
    def test_no_remote_url_found(self, mock_run) -> None:
        """Return None when remote URL is not found."""
        mock_run.side_effect = [
            # branch.main.remote -> upstream
            type("Result", (), {"returncode": 0, "stdout": "upstream\n"})(),
            # remote.upstream.url -> not found
            type("Result", (), {"returncode": 1, "stdout": ""})(),
        ]

        result = get_branch_remote_url("main")

        assert result is None

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_no_branch_uses_get_current_branch(self, mock_run, mock_branch) -> None:
        """Use get_current_branch() when no branch is specified."""
        mock_branch.return_value = "develop"
        mock_run.side_effect = [
            # branch.develop.remote -> origin
            type("Result", (), {"returncode": 0, "stdout": "origin\n"})(),
            # remote.origin.url
            type(
                "Result",
                (),
                {"returncode": 0, "stdout": "https://github.com/user/repo.git\n"},
            )(),
        ]

        result = get_branch_remote_url()

        mock_branch.assert_called_once()
        assert mock_run.call_args_list[0][0][0] == [
            "git",
            "config",
            "--get",
            "branch.develop.remote",
        ]
        assert result == "https://github.com/user/repo.git"

    @patch("paude.git_remote.subprocess.run")
    def test_cwd_passed_to_subprocess(self, mock_run) -> None:
        """Pass cwd to subprocess.run calls."""
        mock_run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": "origin\n"})(),
            type(
                "Result",
                (),
                {"returncode": 0, "stdout": "https://github.com/user/repo.git\n"},
            )(),
        ]

        get_branch_remote_url("main", cwd="/some/repo")

        for call in mock_run.call_args_list:
            assert call[1]["cwd"] == "/some/repo"


class TestResolveOriginCmd:
    """Tests for resolve_origin_cmd."""

    @patch("paude.git_remote.get_branch_remote_url")
    def test_returns_set_origin_cmd(self, mock_get_url) -> None:
        """Return a set-origin command when URL is found."""
        mock_get_url.return_value = "git@github.com:user/repo.git"

        result = resolve_origin_cmd("main", cwd="/some/repo")

        assert result is not None
        assert "https://github.com/user/repo.git" in result
        assert "git -C /pvc/workspace remote" in result
        mock_get_url.assert_called_once_with("main", cwd="/some/repo")

    @patch("paude.git_remote.get_branch_remote_url")
    def test_returns_none_when_no_url(self, mock_get_url) -> None:
        """Return None when no remote URL is found."""
        mock_get_url.return_value = None

        result = resolve_origin_cmd("main")

        assert result is None


class TestBuildCloneFromOriginCmd:
    """Tests for _build_clone_from_origin_cmd."""

    def test_builds_clone_command(self) -> None:
        """Build clone command with HTTPS URL."""
        cmd = _build_clone_from_origin_cmd("https://github.com/user/repo.git")
        assert "git clone" in cmd
        assert "https://github.com/user/repo.git" in cmd
        assert "/pvc/workspace" in cmd
        assert "receive.denyCurrentBranch updateInstead" in cmd

    def test_quotes_url_with_special_chars(self) -> None:
        """Quote URLs with special characters."""
        cmd = _build_clone_from_origin_cmd("https://example.com/path with spaces")
        # shlex.quote wraps in single quotes
        assert "'" in cmd


class TestCloneFromOriginPodman:
    """Tests for clone_from_origin_podman."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when clone succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = clone_from_origin_podman(
            "paude-test", "https://github.com/user/repo.git"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "git clone" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when clone fails (private repo)."""
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: repository not found"

        result = clone_from_origin_podman(
            "paude-test", "https://github.com/user/private-repo.git"
        )

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run) -> None:
        """Return False when clone times out."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=600)

        result = clone_from_origin_podman(
            "paude-test", "https://github.com/user/repo.git"
        )

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_uses_timeout(self, mock_run) -> None:
        """Pass timeout to subprocess."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        clone_from_origin_podman("paude-test", "https://github.com/user/repo.git")

        from paude.constants import CLONE_FROM_ORIGIN_TIMEOUT

        assert mock_run.call_args[1]["timeout"] == CLONE_FROM_ORIGIN_TIMEOUT


class TestCloneFromOriginOpenshift:
    """Tests for clone_from_origin_openshift."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when clone succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = clone_from_origin_openshift(
            "pod-0", "namespace", "https://github.com/user/repo.git"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "oc" in call_args
        assert "pod-0" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "git clone" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_with_context(self, mock_run) -> None:
        """Include context when specified."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        result = clone_from_origin_openshift(
            "pod-0", "ns", "https://github.com/user/repo.git", context="my-ctx"
        )

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when clone fails."""
        mock_run.return_value.returncode = 128
        mock_run.return_value.stderr = "fatal: repository not found"

        result = clone_from_origin_openshift(
            "pod-0", "namespace", "https://github.com/user/private-repo.git"
        )

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run) -> None:
        """Return False when clone times out."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=600)

        result = clone_from_origin_openshift(
            "pod-0", "namespace", "https://github.com/user/repo.git"
        )

        assert result is False


class TestCountLocalOnlyCommits:
    """Tests for count_local_only_commits."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_count_when_ahead(self, mock_run) -> None:
        """Return commit count when local is ahead of origin."""
        from paude.git_remote import count_local_only_commits

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "3\n"

        result = count_local_only_commits("main")

        assert result == 3
        mock_run.assert_called_once_with(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            capture_output=True,
            text=True,
        )

    @patch("paude.git_remote.subprocess.run")
    def test_returns_zero_when_at_origin(self, mock_run) -> None:
        """Return 0 when local is at or behind origin."""
        from paude.git_remote import count_local_only_commits

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "0\n"

        result = count_local_only_commits("main")

        assert result == 0

    @patch("paude.git_remote.subprocess.run")
    def test_returns_none_when_no_tracking(self, mock_run) -> None:
        """Return None when origin ref doesn't exist."""
        from paude.git_remote import count_local_only_commits

        mock_run.return_value.returncode = 128

        result = count_local_only_commits("main")

        assert result is None

    @patch("paude.git_remote.subprocess.run")
    def test_returns_none_on_invalid_output(self, mock_run) -> None:
        """Return None when output is not a number."""
        from paude.git_remote import count_local_only_commits

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "not-a-number\n"

        result = count_local_only_commits("main")

        assert result is None


class TestGitPushToRemoteQuiet:
    """Tests for git_push_to_remote quiet parameter."""

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_quiet_captures_output(self, mock_run, mock_branch) -> None:
        """When quiet=True, capture_output should be True."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0

        git_push_to_remote("paude-test", quiet=True)

        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is True

    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_default_shows_output(self, mock_run, mock_branch) -> None:
        """By default, capture_output should be False."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0

        git_push_to_remote("paude-test")

        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is False
