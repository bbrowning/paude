"""Tests for git_remote module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from paude.git_remote import (
    _build_clone_from_origin_cmd,
    _build_set_base_ref_cmd,
    _build_set_origin_cmd,
    _build_workspace_init_cmd,
    _exec_in_container,
    build_podman_remote_url,
    build_ssh_remote_url,
    clone_from_origin,
    enable_ext_protocol,
    get_branch_remote_url,
    get_current_branch,
    get_upstream_url,
    git_diff_stat,
    git_fetch_from_remote,
    git_push_tags_to_remote,
    git_remote_add,
    git_remote_exists,
    git_remote_get_url,
    git_remote_remove,
    initialize_container_workspace,
    is_ext_protocol_allowed,
    is_git_repository,
    list_paude_remotes,
    podman_exec_builder,
    resolve_local_git_identity,
    resolve_origin_cmd,
    resolve_session_remote,
    set_base_ref_in_container,
    set_origin_in_container,
    setup_precommit_in_container,
    ssh_url_to_https,
)


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


class TestGitRemoteExists:
    """Tests for git_remote_exists."""

    @patch("paude.git_remote.subprocess.run")
    def test_true_when_present(self, mock_run) -> None:
        """Returns True and queries by exact remote name."""
        mock_run.return_value.returncode = 0

        assert git_remote_exists("rig-vllm") is True
        assert mock_run.call_args[0][0] == ["git", "remote", "get-url", "rig-vllm"]

    @patch("paude.git_remote.subprocess.run")
    def test_false_when_absent(self, mock_run) -> None:
        """Returns False when the remote is unknown."""
        mock_run.return_value.returncode = 2

        assert git_remote_exists("rig-vllm") is False

    @patch("paude.git_remote.subprocess.run")
    def test_forwards_cwd(self, mock_run) -> None:
        """Existence check runs in the given host repo."""
        mock_run.return_value.returncode = 0

        git_remote_exists("rig-vllm", cwd=Path("/host/vllm"))

        assert mock_run.call_args.kwargs["cwd"] == Path("/host/vllm")


class TestGitRemoteGetUrl:
    """Tests for git_remote_get_url."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_url_when_present(self, mock_run) -> None:
        """Returns the trimmed URL and queries by exact remote name."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "ext::podman exec -i c %S /pvc/workspace\n"

        result = git_remote_get_url("rig-vllm")

        assert result == "ext::podman exec -i c %S /pvc/workspace"
        assert mock_run.call_args[0][0] == ["git", "remote", "get-url", "rig-vllm"]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_none_when_absent(self, mock_run) -> None:
        """Returns None when the remote is unknown."""
        mock_run.return_value.returncode = 2
        mock_run.return_value.stdout = ""

        assert git_remote_get_url("rig-vllm") is None

    @patch("paude.git_remote.subprocess.run")
    def test_returns_none_on_empty_url(self, mock_run) -> None:
        """Returns None rather than an empty string."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "\n"

        assert git_remote_get_url("rig-vllm") is None

    @patch("paude.git_remote.subprocess.run")
    def test_forwards_cwd(self, mock_run) -> None:
        """Lookup runs in the given host repo."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "url\n"

        git_remote_get_url("rig-vllm", cwd=Path("/host/vllm"))

        assert mock_run.call_args.kwargs["cwd"] == Path("/host/vllm")


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
    def test_add_forwards_cwd(self, mock_run) -> None:
        """Remote is added in the given host repo."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        git_remote_add("rig-vllm", "ext::...", cwd=Path("/host/vllm"))

        assert mock_run.call_args.kwargs["cwd"] == Path("/host/vllm")

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
paude-other\text::docker exec paude-other %S /pvc/workspace (fetch)
paude-other\text::docker exec paude-other %S /pvc/workspace (push)
"""

        remotes = list_paude_remotes()

        assert len(remotes) == 2
        assert (
            "paude-my-session",
            "ext::podman exec paude-my-session %S /pvc/workspace",
        ) in remotes
        assert (
            "paude-other",
            "ext::docker exec paude-other %S /pvc/workspace",
        ) in remotes

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

    @patch("paude.git_remote.subprocess.run")
    def test_forwards_cwd(self, mock_run) -> None:
        """The check runs in the given host repo."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "always\n"

        is_ext_protocol_allowed(cwd=Path("/host/vllm"))

        assert mock_run.call_args.kwargs["cwd"] == Path("/host/vllm")


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

    @patch("paude.git_remote.subprocess.run")
    def test_forwards_cwd(self, mock_run) -> None:
        """ext:: is enabled in the given host repo."""
        mock_run.return_value.returncode = 0

        enable_ext_protocol(cwd=Path("/host/vllm"))

        assert mock_run.call_args.kwargs["cwd"] == Path("/host/vllm")


class TestInitializeContainerWorkspacePodman:
    """Tests for initialize_container_workspace with podman exec builder."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when git init succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        result = initialize_container_workspace(eb)

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when git init fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        eb = podman_exec_builder("paude-test", "podman")
        result = initialize_container_workspace(eb)

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_uses_branch_name(self, mock_run) -> None:
        """Use specified branch name in git init."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        result = initialize_container_workspace(eb, branch="develop")

        assert result is True
        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "git init -b develop" in bash_cmd


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


class TestSetBaseRefInContainerPodman:
    """Tests for set_base_ref_in_container with podman exec builder."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting base ref succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        result = set_base_ref_in_container(eb)

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "update-ref refs/paude/base HEAD" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting base ref fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        eb = podman_exec_builder("paude-test", "podman")
        result = set_base_ref_in_container(eb)

        assert result is False


class TestGitPushToRemote:
    """Tests for git_push_to_remote."""

    @patch("paude.git_remote.utils.get_current_branch")
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

    @patch("paude.git_remote.utils.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_uses_specified_branch(self, mock_run, mock_branch) -> None:
        """Use specified branch instead of current."""
        from paude.git_remote import git_push_to_remote

        mock_run.return_value.returncode = 0

        result = git_push_to_remote("paude-test", "feature-branch")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "push", "paude-test", "feature-branch"]

    @patch("paude.git_remote.utils.get_current_branch")
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
    """Tests for set_origin_in_container with podman exec builder."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when setting origin succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        result = set_origin_in_container(eb, "https://github.com/user/repo")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting origin fails."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        eb = podman_exec_builder("paude-test", "podman")
        result = set_origin_in_container(eb, "https://github.com/user/repo")

        assert result is False


class TestSetupPrecommitInContainerPodman:
    """Tests for setup_precommit_in_container with podman exec builder."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when pre-commit install succeeds."""
        mock_run.return_value.returncode = 0

        eb = podman_exec_builder("paude-test", "podman")
        result = setup_precommit_in_container(eb)

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args

    @patch("paude.git_remote.subprocess.run")
    def test_runs_precommit_install(self, mock_run) -> None:
        """Run pre-commit install command in container."""
        mock_run.return_value.returncode = 0

        eb = podman_exec_builder("paude-test", "podman")
        setup_precommit_in_container(eb)

        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        bash_cmd = call_args[bash_cmd_idx]
        assert "pre-commit install" in bash_cmd
        assert ".pre-commit-config.yaml" in bash_cmd

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when command fails."""
        mock_run.return_value.returncode = 1

        eb = podman_exec_builder("paude-test", "podman")
        result = setup_precommit_in_container(eb)

        assert result is False


class TestBuildPodmanExecCmd:
    """Tests for podman_exec_builder."""

    def test_builds_correct_command(self) -> None:
        """Build correct podman exec command."""
        eb = podman_exec_builder("my-container", "podman")
        result = eb("echo hello")
        assert result == ["podman", "exec", "my-container", "bash", "-c", "echo hello"]


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

    def test_build_workspace_init_cmd_custom_path(self) -> None:
        """Init command targets a custom container repo path."""
        cmd = _build_workspace_init_cmd("main", "/pvc/workspace/rigs/vllm")
        assert "git init -b main /pvc/workspace/rigs/vllm" in cmd
        assert "git -C /pvc/workspace/rigs/vllm config" in cmd

    def test_build_set_base_ref_cmd(self) -> None:
        """Base-ref command defaults to the top-level workspace."""
        cmd = _build_set_base_ref_cmd()
        assert cmd == "git -C /pvc/workspace update-ref refs/paude/base HEAD"

    def test_build_set_base_ref_cmd_custom_path(self) -> None:
        """Base-ref command targets a custom container repo path."""
        cmd = _build_set_base_ref_cmd("/pvc/workspace/rigs/vllm")
        assert cmd == (
            "git -C /pvc/workspace/rigs/vllm update-ref refs/paude/base HEAD"
        )

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

    @patch("paude.git_remote.utils.get_current_branch")
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


class TestGetUpstreamUrl:
    """Tests for get_upstream_url."""

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_prefers_main_branch_remote(self, mock_get_url) -> None:
        """Use main branch's tracking remote when available."""
        mock_get_url.return_value = "https://github.com/upstream/repo.git"

        result = get_upstream_url()

        assert result == "https://github.com/upstream/repo.git"
        mock_get_url.assert_called_once_with("main", cwd=None)

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_falls_back_to_master(self, mock_get_url) -> None:
        """Fall back to master branch when main has no remote."""
        mock_get_url.side_effect = [None, "https://github.com/upstream/repo.git"]

        result = get_upstream_url()

        assert result == "https://github.com/upstream/repo.git"
        assert mock_get_url.call_count == 2
        mock_get_url.assert_any_call("main", cwd=None)
        mock_get_url.assert_any_call("master", cwd=None)

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_falls_back_to_current_branch(self, mock_get_url) -> None:
        """Fall back to current branch when main/master have no remotes."""
        mock_get_url.side_effect = [
            None,  # main
            None,  # master
            "https://github.com/fork/repo.git",  # current branch
        ]

        result = get_upstream_url()

        assert result == "https://github.com/fork/repo.git"
        assert mock_get_url.call_count == 3
        mock_get_url.assert_any_call(None, cwd=None)

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_returns_none_when_no_remotes(self, mock_get_url) -> None:
        """Return None when no remotes can be resolved."""
        mock_get_url.return_value = None

        result = get_upstream_url()

        assert result is None

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_passes_cwd(self, mock_get_url) -> None:
        """Pass cwd to get_branch_remote_url."""
        mock_get_url.return_value = "https://github.com/upstream/repo.git"

        get_upstream_url(cwd="/some/repo")

        mock_get_url.assert_called_once_with("main", cwd="/some/repo")

    @patch("paude.git_remote.utils.get_branch_remote_url")
    def test_fork_workflow_uses_upstream_not_fork(self, mock_get_url) -> None:
        """In a fork workflow, prefer main's upstream remote over current branch's origin."""
        # Simulate: main tracks upstream (vllm-project/vllm),
        # feature branch tracks origin (bbrowning/vllm)
        mock_get_url.side_effect = lambda branch, cwd=None: {
            "main": "https://github.com/vllm-project/vllm.git",
            "feature-branch": "https://github.com/bbrowning/vllm.git",
        }.get(branch)

        result = get_upstream_url()

        assert result == "https://github.com/vllm-project/vllm.git"


class TestResolveOriginCmd:
    """Tests for resolve_origin_cmd."""

    @patch("paude.git_remote.utils.get_upstream_url")
    def test_returns_set_origin_cmd(self, mock_get_url) -> None:
        """Return a set-origin command when URL is found."""
        mock_get_url.return_value = "git@github.com:user/repo.git"

        result = resolve_origin_cmd(cwd="/some/repo")

        assert result is not None
        assert "https://github.com/user/repo.git" in result
        assert "git -C /pvc/workspace remote" in result
        mock_get_url.assert_called_once_with(cwd="/some/repo")

    @patch("paude.git_remote.utils.get_upstream_url")
    def test_returns_none_when_no_url(self, mock_get_url) -> None:
        """Return None when no remote URL is found."""
        mock_get_url.return_value = None

        result = resolve_origin_cmd()

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
    """Tests for clone_from_origin with podman exec builder."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when clone succeeds."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        result = clone_from_origin(eb, "https://github.com/user/repo.git")

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

        eb = podman_exec_builder("paude-test", "podman")
        result = clone_from_origin(eb, "https://github.com/user/private-repo.git")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run) -> None:
        """Return False when clone times out."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=600)

        eb = podman_exec_builder("paude-test", "podman")
        result = clone_from_origin(eb, "https://github.com/user/repo.git")

        assert result is False

    @patch("paude.git_remote.subprocess.run")
    def test_uses_timeout(self, mock_run) -> None:
        """Pass timeout to subprocess."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        eb = podman_exec_builder("paude-test", "podman")
        clone_from_origin(eb, "https://github.com/user/repo.git")

        from paude.constants import CLONE_FROM_ORIGIN_TIMEOUT

        assert mock_run.call_args[1]["timeout"] == CLONE_FROM_ORIGIN_TIMEOUT


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

    @patch("paude.git_remote.utils.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_quiet_captures_output(self, mock_run, mock_branch) -> None:
        """When quiet=True, capture_output should be True."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0

        git_push_to_remote("paude-test", quiet=True)

        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is True

    @patch("paude.git_remote.utils.get_current_branch")
    @patch("paude.git_remote.subprocess.run")
    def test_default_shows_output(self, mock_run, mock_branch) -> None:
        """By default, capture_output should be False."""
        from paude.git_remote import git_push_to_remote

        mock_branch.return_value = "main"
        mock_run.return_value.returncode = 0

        git_push_to_remote("paude-test")

        _, kwargs = mock_run.call_args
        assert kwargs["capture_output"] is False


class TestBuildSshRemoteUrl:
    """Tests for build_ssh_remote_url."""

    def test_basic_ssh_url(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-my-session",
            ssh_host="user@gpu-server",
        )
        assert url == (
            "ext::ssh user@gpu-server docker exec -i paude-my-session %S /pvc/workspace"
        )

    def test_with_ssh_key(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-test",
            ssh_host="user@host",
            ssh_key="/home/user/.ssh/id_rsa",
        )
        assert "-i /home/user/.ssh/id_rsa" in url
        assert "user@host" in url

    def test_with_ssh_port(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-test",
            ssh_host="user@host",
            ssh_port=2222,
        )
        assert "-p 2222" in url

    def test_with_key_and_port(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-test",
            ssh_host="user@host",
            ssh_key="/key",
            ssh_port=2222,
        )
        assert "-i /key" in url
        assert "-p 2222" in url

    def test_custom_engine(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-test",
            ssh_host="user@host",
            engine="podman",
        )
        assert "podman exec -i" in url

    def test_custom_workspace(self) -> None:
        url = build_ssh_remote_url(
            container_name="paude-test",
            ssh_host="user@host",
            workspace_path="/custom/path",
        )
        assert url.endswith("/custom/path")


class TestResolveSessionRemote:
    """Tests for resolve_session_remote."""

    @patch("paude.registry.SessionRegistry")
    def test_no_registry_entry_uses_podman_url(self, mock_registry_class) -> None:
        """No registry entry falls back to a local podman/docker URL, no transport."""
        mock_registry_class.return_value.get.return_value = None

        url, transport = resolve_session_remote(
            "my-session", "paude-my-session", "podman"
        )

        assert url == "ext::podman exec -i paude-my-session %S /pvc/workspace"
        assert transport is None

    @patch("paude.registry.SessionRegistry")
    def test_local_session_uses_podman_url(self, mock_registry_class) -> None:
        """A registry entry without ssh_host is treated as local."""
        mock_registry_class.return_value.get.return_value = MagicMock(
            ssh_host=None, ssh_key=None
        )

        url, transport = resolve_session_remote(
            "my-session", "paude-my-session", "docker"
        )

        assert url == "ext::docker exec -i paude-my-session %S /pvc/workspace"
        assert transport is None

    @patch("paude.registry.SessionRegistry")
    def test_ssh_host_session_uses_ssh_url(self, mock_registry_class) -> None:
        """A --host session builds an SSH-wrapped URL and a matching transport."""
        mock_registry_class.return_value.get.return_value = MagicMock(
            ssh_host="user@gpu-server:2222", ssh_key="/home/user/.ssh/id_ed25519"
        )

        url, transport = resolve_session_remote(
            "my-session", "paude-my-session", "docker"
        )

        assert url == (
            "ext::ssh -i /home/user/.ssh/id_ed25519 -p 2222 user@gpu-server "
            "docker exec -i paude-my-session %S /pvc/workspace"
        )
        assert transport is not None
        assert transport.host == "user@gpu-server"
        assert transport.port == 2222
        assert transport.key == "/home/user/.ssh/id_ed25519"

    @patch("paude.registry.SessionRegistry")
    def test_forwards_workspace_path_local(self, mock_registry_class) -> None:
        """workspace_path selects the container repo path in the local URL."""
        mock_registry_class.return_value.get.return_value = None

        url, _transport = resolve_session_remote(
            "my-session",
            "paude-my-session",
            "podman",
            workspace_path="/pvc/workspace/rigs/vllm",
        )

        assert url == (
            "ext::podman exec -i paude-my-session %S /pvc/workspace/rigs/vllm"
        )

    @patch("paude.registry.SessionRegistry")
    def test_forwards_workspace_path_ssh(self, mock_registry_class) -> None:
        """workspace_path is threaded into the SSH-wrapped URL too."""
        mock_registry_class.return_value.get.return_value = MagicMock(
            ssh_host="user@gpu-server", ssh_key=None
        )

        url, _transport = resolve_session_remote(
            "my-session",
            "paude-my-session",
            "docker",
            workspace_path="/pvc/workspace/rigs/vllm",
        )

        assert url.endswith(
            "docker exec -i paude-my-session %S /pvc/workspace/rigs/vllm"
        )


class TestExecCmdBuilders:
    """Tests for container-engine exec command builders."""

    def test_podman_exec_builder_default_engine(self) -> None:
        """Build podman exec command with default engine."""
        from paude.git_remote import podman_exec_builder

        builder = podman_exec_builder("my-container")
        result = builder("echo hello")
        assert result == ["podman", "exec", "my-container", "bash", "-c", "echo hello"]

    def test_podman_exec_builder_docker_engine(self) -> None:
        """Build docker exec command."""
        from paude.git_remote import podman_exec_builder

        builder = podman_exec_builder("my-container", engine="docker")
        result = builder("echo hello")
        assert result == ["docker", "exec", "my-container", "bash", "-c", "echo hello"]

    def test_builder_is_callable(self) -> None:
        """ExecCmdBuilder is callable."""
        from paude.git_remote import ExecCmdBuilder, podman_exec_builder

        builder: ExecCmdBuilder = podman_exec_builder("c")
        assert callable(builder)


class TestUnifiedInitializeContainerWorkspace:
    """Tests for the unified initialize_container_workspace function."""

    @patch("paude.git_remote.subprocess.run")
    def test_with_podman_builder(self, mock_run) -> None:
        """Initialize workspace using podman exec builder."""
        from paude.git_remote import initialize_container_workspace, podman_exec_builder

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        builder = podman_exec_builder("paude-test")
        result = initialize_container_workspace(builder, branch="develop")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["podman", "exec"]
        assert "paude-test" in call_args
        bash_cmd_idx = call_args.index("-c") + 1
        assert "git init -b develop" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when initialization fails."""
        from paude.git_remote import initialize_container_workspace, podman_exec_builder

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "exec error"

        builder = podman_exec_builder("paude-test")
        result = initialize_container_workspace(builder)

        assert result is False


class TestUnifiedSetOriginInContainer:
    """Tests for the unified set_origin_in_container function."""

    @patch("paude.git_remote.subprocess.run")
    def test_sets_origin_with_podman(self, mock_run) -> None:
        """Set origin using podman exec builder."""
        from paude.git_remote import podman_exec_builder, set_origin_in_container

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        builder = podman_exec_builder("paude-test", engine="docker")
        result = set_origin_in_container(builder, "https://github.com/user/repo")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0:2] == ["docker", "exec"]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_failure(self, mock_run) -> None:
        """Return False when setting origin fails."""
        from paude.git_remote import podman_exec_builder, set_origin_in_container

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "error"

        builder = podman_exec_builder("paude-test")
        result = set_origin_in_container(builder, "https://example.com/repo")

        assert result is False


class TestUnifiedSetBaseRefInContainer:
    """Tests for the unified set_base_ref_in_container function."""

    @patch("paude.git_remote.subprocess.run")
    def test_sets_base_ref(self, mock_run) -> None:
        """Set base ref using exec builder."""
        from paude.git_remote import podman_exec_builder, set_base_ref_in_container

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        builder = podman_exec_builder("paude-test")
        result = set_base_ref_in_container(builder)

        assert result is True
        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        assert "update-ref refs/paude/base HEAD" in call_args[bash_cmd_idx]


class TestUnifiedSetupPrecommitInContainer:
    """Tests for the unified setup_precommit_in_container function."""

    @patch("paude.git_remote.subprocess.run")
    def test_without_set_home(self, mock_run) -> None:
        """Run pre-commit install without HOME override."""
        from paude.git_remote import podman_exec_builder, setup_precommit_in_container

        mock_run.return_value.returncode = 0

        builder = podman_exec_builder("paude-test")
        result = setup_precommit_in_container(builder)

        assert result is True
        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        assert "pre-commit install" in call_args[bash_cmd_idx]
        assert "export HOME=" not in call_args[bash_cmd_idx]


class TestUnifiedCloneFromOrigin:
    """Tests for the unified clone_from_origin function."""

    @patch("paude.git_remote.subprocess.run")
    def test_returns_true_on_success(self, mock_run) -> None:
        """Return True when clone succeeds."""
        from paude.git_remote import clone_from_origin, podman_exec_builder

        mock_run.return_value.returncode = 0
        mock_run.return_value.stderr = ""

        builder = podman_exec_builder("paude-test")
        result = clone_from_origin(builder, "https://github.com/user/repo.git")

        assert result is True
        call_args = mock_run.call_args[0][0]
        bash_cmd_idx = call_args.index("-c") + 1
        assert "git clone" in call_args[bash_cmd_idx]

    @patch("paude.git_remote.subprocess.run")
    def test_returns_false_on_timeout(self, mock_run) -> None:
        """Return False when clone times out."""
        import subprocess

        from paude.git_remote import clone_from_origin, podman_exec_builder

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git clone", timeout=600)

        builder = podman_exec_builder("paude-test")
        result = clone_from_origin(builder, "https://github.com/user/repo.git")

        assert result is False


class TestResolveLocalGitIdentity:
    """Tests for resolve_local_git_identity()."""

    @patch("paude.git_remote.utils.subprocess.run")
    def test_returns_name_and_email(self, mock_run) -> None:
        """Both values are returned, stripped of trailing whitespace."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="Ada Lovelace\n"),
            MagicMock(returncode=0, stdout="ada@example.com\n"),
        ]

        assert resolve_local_git_identity() == ("Ada Lovelace", "ada@example.com")

    @patch("paude.git_remote.utils.subprocess.run")
    def test_unset_values_return_none(self, mock_run) -> None:
        """A non-zero git exit maps to None for that field."""
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=1, stdout=""),
        ]

        assert resolve_local_git_identity() == (None, None)

    @patch("paude.git_remote.utils.subprocess.run")
    def test_blank_value_maps_to_none(self, mock_run) -> None:
        """A blank value is treated as unset."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="   \n"),
            MagicMock(returncode=0, stdout="ada@example.com\n"),
        ]

        assert resolve_local_git_identity() == (None, "ada@example.com")

    @patch("paude.git_remote.utils.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_git_returns_none(self, mock_run) -> None:
        """A missing git binary is swallowed rather than raised."""
        assert resolve_local_git_identity() == (None, None)
