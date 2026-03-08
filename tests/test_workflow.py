"""Tests for the workflow module."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import click.exceptions
import pytest

from paude.workflow import (
    _get_container_branch,
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
        # checkout existing fails, create new succeeds, merge succeeds
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=1, stdout="", stderr="not found"),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        harvest_session("test", "my-branch")

        mock_fetch.assert_called_once()
        # checkout attempt + create branch + merge
        assert mock_run.call_count == 3

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
    def test_harvest_merge_failure(
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

        # checkout existing succeeds, merge fails
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=1, stdout="", stderr="conflict"),
        ]

        with pytest.raises(click.exceptions.Exit):
            harvest_session("test", "my-branch")

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_existing_branch(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Re-harvest into an existing branch picks up new changes."""
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True
        mock_diff.return_value = " 1 file changed\n"
        # checkout existing succeeds, merge succeeds
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]

        harvest_session("test", "my-branch")

        mock_fetch.assert_called_once()
        # checkout existing + merge (no create)
        assert mock_run.call_count == 2

    @patch("paude.workflow.subprocess.run")
    @patch("paude.git_remote.git_diff_stat")
    @patch("paude.git_remote.git_fetch_from_remote")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_harvest_already_up_to_date(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
        mock_fetch: MagicMock,
        mock_diff: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Re-harvest with no new changes reports up to date."""
        self._setup_mocks(mock_find, tmp_path)
        mock_list.return_value = [("paude-test", "ext::...")]
        mock_fetch.return_value = True
        mock_diff.return_value = ""
        mock_run.side_effect = [
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(
                args=[],
                returncode=0,
                stdout="Already up to date.\n",
                stderr="",
            ),
        ]

        harvest_session("test", "my-branch")


class TestStatusSessions:
    """Tests for status_sessions."""

    @patch("paude.session_status.get_session_activity")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_shows_sessions(
        self,
        mock_collect: MagicMock,
        mock_activity: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from paude.backends.base import Session
        from paude.session_status import SessionActivity

        mock_session = Session(
            name="test-session",
            status="running",
            workspace=Path("/workspace/myproject"),
            created_at="2026-01-01T00:00:00Z",
            backend_type="podman",
        )
        mock_backend = MagicMock()
        mock_collect.return_value = [(mock_session, mock_backend)]
        mock_activity.return_value = SessionActivity(
            last_activity="2m ago", state="Active"
        )

        status_sessions()

        captured = capsys.readouterr()
        assert "test-session" in captured.out
        assert "Active" in captured.out
        assert "myproject" in captured.out

    @patch("paude.session_discovery.collect_all_sessions")
    def test_no_sessions(
        self,
        mock_collect: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_collect.return_value = []

        status_sessions()

        captured = capsys.readouterr()
        assert "No sessions found" in captured.out

    @patch("paude.session_status.get_session_activity")
    @patch("paude.session_discovery.collect_all_sessions")
    def test_stopped_session_shows_stopped(
        self,
        mock_collect: MagicMock,
        mock_activity: MagicMock,
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
        mock_collect.return_value = [(mock_session, MagicMock())]

        status_sessions()

        captured = capsys.readouterr()
        assert "Stopped" in captured.out
        # Activity should not be queried for stopped sessions
        mock_activity.assert_not_called()


class TestResetSession:
    """Tests for reset_session."""

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_reset_success(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (0, "", "")
        mock_find.return_value = ("podman", mock_backend)
        mock_list.return_value = []

        reset_session("test", force=True)

        # Should exec reset cmd and clear cmd
        assert mock_backend.exec_in_session.call_count == 2

    @patch("paude.cli.find_session_backend")
    def test_reset_not_running(self, mock_find: MagicMock) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "stopped"
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = ("podman", mock_backend)

        with pytest.raises(click.exceptions.Exit):
            reset_session("test")

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_reset_keeps_conversation(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (0, "", "")
        mock_find.return_value = ("podman", mock_backend)
        mock_list.return_value = []

        reset_session("test", force=True, keep_conversation=True)

        # Only reset cmd, no clear cmd
        assert mock_backend.exec_in_session.call_count == 1

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_reset_unmerged_work_blocks(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (0, "abc1234 Some work\n", "")
        mock_find.return_value = ("podman", mock_backend)
        mock_list.return_value = [("paude-test", "ext::...")]

        with pytest.raises(click.exceptions.Exit):
            reset_session("test")

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.cli.find_session_backend")
    def test_reset_exec_failure(
        self,
        mock_find: MagicMock,
        mock_list: MagicMock,
    ) -> None:
        mock_backend = MagicMock()
        mock_session = MagicMock()
        mock_session.status = "running"
        mock_backend.get_session.return_value = mock_session
        mock_backend.exec_in_session.return_value = (1, "", "error")
        mock_find.return_value = ("podman", mock_backend)
        mock_list.return_value = []

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
