"""Tests for utility functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from paude.utils import (
    RequirementError,
    check_git_safety,
    check_requirements,
    resolve_path,
)


class TestResolvePath:
    """Tests for resolve_path."""

    def test_resolves_symlinks(self, tmp_path: Path):
        """resolve_path resolves symlinks to physical path."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        symlink = tmp_path / "link.txt"
        symlink.symlink_to(real_file)

        result = resolve_path(symlink)
        assert result == real_file.resolve()

    def test_returns_none_for_nonexistent(self, tmp_path: Path):
        """resolve_path returns None for non-existent path."""
        nonexistent = tmp_path / "does_not_exist.txt"
        result = resolve_path(nonexistent)
        assert result is None


class TestCheckRequirements:
    """Tests for check_requirements."""

    @patch("paude.utils.shutil.which")
    def test_raises_when_podman_missing(self, mock_which):
        """check_requirements raises when podman missing."""
        mock_which.return_value = None
        with pytest.raises(RequirementError):
            check_requirements()

    @patch("paude.utils.shutil.which")
    def test_passes_when_podman_present(self, mock_which):
        """check_requirements passes when podman is present."""
        mock_which.return_value = "/usr/bin/podman"
        # Should not raise
        check_requirements()


class TestCheckGitSafety:
    """Tests for check_git_safety."""

    def test_warns_when_no_git_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """check_git_safety warns when no .git directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # No .git directory

        check_git_safety(workspace)
        captured = capsys.readouterr()
        assert "No git repository" in captured.err

    def test_warns_when_no_remotes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """check_git_safety warns when no remotes configured."""
        import subprocess

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Initialize git repo without remotes
        subprocess.run(["git", "init"], cwd=workspace, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=workspace,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=workspace,
            capture_output=True,
        )

        check_git_safety(workspace)
        captured = capsys.readouterr()
        assert "no remotes" in captured.err
