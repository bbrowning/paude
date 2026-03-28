"""Tests for the workflow module."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import click.exceptions
import pytest

from paude.workflow import (
    _get_container_branch,
    _validate_harvest_branch,
    harvest_session,
    reset_session,
    status_sessions,
)


class TestGetContainerBranch:
    """Tests for _get_container_branch."""

    def test_returns_branch_name(self) -> None:
        mock_backend = MagicMock()
        mock_backend.exec_in_session.return_value = (0, "feature-branch\n", "")

        result = _get_container_branch(mock_backend, "my-session")

        assert result == "feature-branch"

    def test_raises_exit_on_failure(self) -> None:
        mock_backend = MagicMock()
        mock_backend.exec_in_session.return_value = (1, "", "error\n")

        with pytest.raises(click.exceptions.Exit):
            _get_container_branch(mock_backend, "my-session")


class TestValidateHarvestBranch:
    """Tests for _validate_harvest_branch."""

    def test_allows_feature_branch(self) -> None:
        _validate_harvest_branch("my-feature")

    @pytest.mark.parametrize(
        "branch",
        ["main", "master", "release", "release-1.0", "release/v2"],
    )
    def test_rejects_protected_branches(self, branch: str) -> None:
        with pytest.raises(click.exceptions.Exit):
            _validate_harvest_branch(branch)


class TestHarvestSession:
    """Tests for harvest_session."""

    def _setup_mocks(
        self,
        mock_find: MagicMock,
        tmp_path: Path,
    ) -> MagicMock:
        """Set up common mocks for harvest tests."""
        (tmp_path / ".git").mkdir(exist_ok=True)

        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.workspace = tmp_path
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (0, "main\n", "")
        mock_find.return_value = ("podman", mock_backend)
        return mock_backend

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_success(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True
        mock_diff.return_value = " 2 files changed\n"
        # checkout -B succeeds
        mock_run.return_value = CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        harvest_session("test", "my-branch")

        mock_fetch.assert_called_once()
        # Single checkout -B call
        assert mock_run.call_count == 1
        args = mock_run.call_args[0][0]
        assert args[:3] == ["git", "checkout", "-B"]

    @patch("paude.cli.find_session_backend")
    def test_harvest_session_not_found(self, mock_find: MagicMock) -> None:
        mock_find.return_value = None

        with pytest.raises(click.exceptions.Exit):
            harvest_session("missing", "my-branch")

    @patch("paude.cli.find_session_backend")
    def test_harvest_workspace_not_found(self, mock_find: MagicMock) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.workspace = Path("/nonexistent/path")
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = ("podman", mock_backend)

        with pytest.raises(click.exceptions.Exit):
            harvest_session("test", "my-branch")

    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_fetch_failure(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        tmp_path: Path,
    ) -> None:
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = False

        with pytest.raises(click.exceptions.Exit):
            harvest_session("test", "my-branch")

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_checkout_failure(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True

        mock_run.return_value = CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )

        with pytest.raises(click.exceptions.Exit):
            harvest_session("test", "my-branch")

    def test_harvest_protected_branch_rejected(self) -> None:
        with pytest.raises(click.exceptions.Exit):
            harvest_session("test", "main")

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_with_pr_uses_force_with_lease(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True
        mock_diff.return_value = " 2 files changed\n"
        # checkout -B, fetch origin, push, pr view
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(
                args=[], returncode=0, stdout="https://pr-url\n", stderr=""
            ),
        ]

        harvest_session("test", "my-branch", create_pr=True)

        # fetch origin is call [1], push is call [2]
        fetch_call = mock_run.call_args_list[1]
        assert fetch_call[0][0] == ["git", "fetch", "origin"]
        push_call = mock_run.call_args_list[2]
        push_args = push_call[0][0]
        assert "--force-with-lease" in push_args

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_creates_pr_when_previous_merged(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """harvest creates a new PR when previous PR for branch was merged."""
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True
        mock_diff.return_value = " 2 files changed\n"
        # checkout -B, fetch origin, push, pr list (no open PRs), pr create
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        harvest_session("test", "my-branch", create_pr=True)

        # pr list returns empty → should call gh pr create
        pr_create_call = mock_run.call_args_list[4]
        pr_create_args = pr_create_call[0][0]
        assert pr_create_args[:3] == ["gh", "pr", "create"]


class TestStatusSessions:
    """Tests for status_sessions."""

    @patch("paude.registry.SessionRegistry.load", return_value={})
    @patch("paude.session_status.get_session_enrichment")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_shows_sessions_with_git_summary(
        self,
        mock_collect: MagicMock,
        mock_enrichment: MagicMock,
        mock_registry_load: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session
        from paude.session_status import SessionActivity, WorkSummary

        mock_session = Session(
            name="test-session",
            status="running",
            workspace=Path("/workspace/myproject"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_backend = MagicMock()
        mock_collect.return_value = ([(mock_session, mock_backend)], {"podman"})
        mock_enrichment.return_value = (
            SessionActivity(
                last_activity="2m ago", state="Active", elapsed_seconds=120
            ),
            WorkSummary(
                branch="feat-auth", commits_ahead=2, latest_subject="Add OAuth"
            ),
        )

        status_sessions()

        captured = capsys.readouterr()
        assert "test-session" in captured.out
        assert "Active" in captured.out
        assert "myproject" in captured.out
        assert "SUMMARY" in captured.out
        assert "feat-auth Add OAuth (+2)" in captured.out

    @patch("paude.registry.SessionRegistry.load", return_value={})
    @patch("paude.session_status.get_session_enrichment")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_shows_changed_files_when_no_commits(
        self,
        mock_collect: MagicMock,
        mock_enrichment: MagicMock,
        mock_registry_load: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session
        from paude.session_status import SessionActivity, WorkSummary

        mock_session = Session(
            name="test-session",
            status="running",
            workspace=Path("/workspace/myproject"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_backend = MagicMock()
        mock_collect.return_value = ([(mock_session, mock_backend)], {"podman"})
        mock_enrichment.return_value = (
            SessionActivity(
                last_activity="2m ago", state="Active", elapsed_seconds=120
            ),
            WorkSummary(
                branch="main",
                commits_ahead=0,
                latest_subject="",
                changed_files=["cli.py", "workflow.py"],
            ),
        )

        status_sessions()

        captured = capsys.readouterr()
        assert "editing: cli.py, workflow.py" in captured.out

    @patch("paude.registry.SessionRegistry.load", return_value={})
    @patch("paude.session_discovery.collect_all_sessions")
    def test_no_sessions(
        self,
        mock_collect: MagicMock,
        mock_registry_load: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collect.return_value = ([], set())

        status_sessions()

        captured = capsys.readouterr()
        assert "No sessions found" in captured.out

    @patch("paude.registry.SessionRegistry.load", return_value={})
    @patch("paude.session_status.get_session_enrichment")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_stopped_sessions_excluded(
        self,
        mock_collect: MagicMock,
        mock_enrichment: MagicMock,
        mock_registry_load: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session

        mock_session = Session(
            name="stopped-session",
            status="stopped",
            workspace=Path("/workspace/project"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_collect.return_value = ([(mock_session, MagicMock())], {"podman"})

        status_sessions()

        captured = capsys.readouterr()
        # Stopped sessions show in the table but are not enriched
        assert "stopped-session" in captured.out
        assert "stopped" in captured.out
        mock_enrichment.assert_not_called()

    @patch("paude.registry.SessionRegistry.load", return_value={})
    @patch("paude.session_status.get_session_enrichment")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_sorts_by_activity(
        self,
        mock_collect: MagicMock,
        mock_enrichment: MagicMock,
        mock_registry_load: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session
        from paude.session_status import SessionActivity

        # Create sessions: stopped, idle running, active running
        stopped = Session(
            name="stopped-ses",
            status="stopped",
            workspace=Path("/workspace/p1"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        idle_running = Session(
            name="idle-ses",
            status="running",
            workspace=Path("/workspace/p2"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        active_running = Session(
            name="active-ses",
            status="running",
            workspace=Path("/workspace/p3"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_backend = MagicMock()
        # Order: stopped first, then idle, then active (wrong order)
        mock_collect.return_value = (
            [
                (stopped, mock_backend),
                (idle_running, mock_backend),
                (active_running, mock_backend),
            ],
            {"podman"},
        )
        mock_enrichment.side_effect = [
            (
                SessionActivity(
                    last_activity="10m ago", state="Idle", elapsed_seconds=600
                ),
                None,
            ),
            (
                SessionActivity(
                    last_activity="5s ago", state="Active", elapsed_seconds=5
                ),
                None,
            ),
        ]

        status_sessions()

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Skip header and separator
        data_lines = lines[2:]
        # active-ses (5s) should be first, idle-ses (10m) second
        # stopped sessions appear last in the output
        assert len(data_lines) == 3
        assert "active-ses" in data_lines[0]
        assert "idle-ses" in data_lines[1]
        assert "stopped-ses" in data_lines[2]

    @patch("paude.session_status.get_session_enrichment")
    @patch("paude.cli.find_session_backend")
    def test_single_session_by_name(
        self,
        mock_find: MagicMock,
        mock_enrichment: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session
        from paude.session_status import SessionActivity, WorkSummary

        mock_session = Session(
            name="remote-1",
            status="running",
            workspace=Path("/workspace/myproject"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = ("podman", mock_backend)
        mock_enrichment.return_value = (
            SessionActivity(last_activity="5s ago", state="Active", elapsed_seconds=5),
            WorkSummary(branch="feat-x", commits_ahead=1, latest_subject="WIP"),
        )

        status_sessions(session_name="remote-1")

        captured = capsys.readouterr()
        assert "remote-1" in captured.out
        assert "Active" in captured.out
        assert "feat-x WIP (+1)" in captured.out
        mock_find.assert_called_once_with("remote-1", None, None, connect_timeout=2)

    @patch("paude.cli.find_session_backend")
    def test_single_session_not_found(
        self,
        mock_find: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_find.return_value = None

        with pytest.raises(click.exceptions.Exit):
            status_sessions(session_name="nonexistent")

        captured = capsys.readouterr()
        assert "Session 'nonexistent' not found" in captured.err


class TestResetSession:
    """Tests for reset_session."""

    def _setup_mocks(
        self,
        mock_find: MagicMock,
    ) -> MagicMock:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_session.agent = "claude"
        mock_session.provider = None
        mock_session.workspace = Path("/fake/workspace")
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (0, "", "")
        mock_find.return_value = ("podman", mock_backend)
        return mock_backend

    def _get_exec_cmds(self, mock_backend: MagicMock) -> list[str]:
        """Return all commands passed to exec_in_session."""
        return [call[0][1] for call in mock_backend.exec_in_session.call_args_list]

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_success(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)

        reset_session("test", force=True)

        # Should exec set-origin, reset cmd (includes base ref update), and clear/cleanup cmd
        assert mock_backend.exec_in_session.call_count == 3

    @patch("paude.cli.find_session_backend")
    def test_reset_not_running(self, mock_find: MagicMock) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "stopped"
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = ("podman", mock_backend)

        with pytest.raises(click.exceptions.Exit):
            reset_session("test")

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_keeps_conversation(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)

        reset_session("test", force=True, keep_conversation=True)

        # set-origin + reset cmd (includes base ref update), no clear cmd or /clear
        assert mock_backend.exec_in_session.call_count == 2

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_sends_clear_to_claude(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)

        reset_session("test", force=True)

        cmds = self._get_exec_cmds(mock_backend)
        clear_cmd = next(c for c in cmds if "tmux" in c)
        assert "tmux send-keys" in clear_cmd
        assert '"/clear"' in clear_cmd
        assert "sleep 0.1" in clear_cmd
        assert clear_cmd.endswith("Enter")

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_preserves_settings(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)

        reset_session("test", force=True)

        cmds = self._get_exec_cmds(mock_backend)
        clear_cmd = next(c for c in cmds if "find" in c)
        assert "settings.local.json" not in clear_cmd
        assert "rm -rf /home/paude/.claude/projects/" not in clear_cmd

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_unmerged_work_blocks(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)
        # set-origin succeeds, fetch+merge-base returns non-zero (diverged), log returns commit
        mock_backend.exec_in_session.side_effect = [
            (0, "", ""),  # set-origin
            (1, "", ""),  # git fetch && git merge-base --is-ancestor (diverged)
            (0, "abc1234 Some work\n", ""),  # git log --oneline -1 HEAD
        ]

        with pytest.raises(click.exceptions.Exit):
            reset_session("test")

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_merged_work_passes(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)
        # set-origin, fetch+merge-base returns 0 (HEAD is ancestor of origin/main),
        # then the reset exec calls
        mock_backend.exec_in_session.side_effect = [
            (0, "", ""),  # set-origin
            (0, "", ""),  # git fetch && git merge-base --is-ancestor (merged)
            (0, "", ""),  # git reset --hard + update-ref
            (0, "", ""),  # clear conversation + /clear
        ]

        reset_session("test")

    @patch(
        "paude.git_remote.resolve_origin_cmd",
        return_value="git remote set-url origin https://github.com/user/repo.git",
    )
    @patch("paude.cli.find_session_backend")
    def test_reset_exec_failure(
        self,
        mock_find: MagicMock,
        mock_remote: MagicMock,
    ) -> None:
        mock_backend = self._setup_mocks(mock_find)
        mock_backend.exec_in_session.return_value = (1, "", "error")

        with pytest.raises(click.exceptions.Exit):
            reset_session("test", force=True)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestHarvestCli:
    """Tests for harvest CLI command."""

    def test_harvest_help(self) -> None:
        from typer.testing import CliRunner

        from paude.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["harvest", "--help"])
        output = _strip_ansi(result.output)
        assert result.exit_code == 0
        assert "harvest" in output.lower()
        assert "--branch" in output
        assert "--pr" in output


class TestResetCli:
    """Tests for reset CLI command."""

    def test_reset_help(self) -> None:
        from typer.testing import CliRunner

        from paude.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["reset", "--help"])
        output = _strip_ansi(result.output)
        assert result.exit_code == 0
        assert "--branch" in output
        assert "--force" in output
        assert "--keep-conversation" in output


class TestStatusCli:
    """Tests for status CLI command."""

    def test_status_help(self) -> None:
        from typer.testing import CliRunner

        from paude.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["status", "--help"])
        output = _strip_ansi(result.output)
        assert result.exit_code == 0
        assert "status" in output.lower()
