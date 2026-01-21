"""Tests for venv detection."""

from __future__ import annotations

from pathlib import Path

from paude.venv import COMMON_VENV_NAMES, find_venvs, is_venv


class TestIsVenv:
    """Tests for is_venv."""

    def test_detects_venv_with_pyvenv_cfg(self, tmp_path: Path):
        """is_venv returns True for directory with pyvenv.cfg."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        assert is_venv(venv) is True

    def test_rejects_directory_without_pyvenv_cfg(self, tmp_path: Path):
        """is_venv returns False for directory without pyvenv.cfg."""
        not_venv = tmp_path / ".venv"
        not_venv.mkdir()

        assert is_venv(not_venv) is False

    def test_rejects_file_named_venv(self, tmp_path: Path):
        """is_venv returns False for file named .venv."""
        fake_venv = tmp_path / ".venv"
        fake_venv.write_text("not a directory")

        assert is_venv(fake_venv) is False

    def test_rejects_nonexistent_path(self, tmp_path: Path):
        """is_venv returns False for nonexistent path."""
        nonexistent = tmp_path / "nonexistent"

        assert is_venv(nonexistent) is False


class TestFindVenvs:
    """Tests for find_venvs."""

    def test_finds_dot_venv(self, tmp_path: Path):
        """find_venvs detects .venv directory."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        result = find_venvs(tmp_path)

        assert len(result) == 1
        assert result[0] == venv

    def test_finds_venv(self, tmp_path: Path):
        """find_venvs detects venv directory."""
        venv = tmp_path / "venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        result = find_venvs(tmp_path)

        assert len(result) == 1
        assert result[0] == venv

    def test_finds_multiple_venvs(self, tmp_path: Path):
        """find_venvs detects multiple venv directories."""
        for name in [".venv", "venv"]:
            venv = tmp_path / name
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        result = find_venvs(tmp_path)

        assert len(result) == 2
        names = [v.name for v in result]
        assert ".venv" in names
        assert "venv" in names

    def test_returns_empty_for_clean_directory(self, tmp_path: Path):
        """find_venvs returns empty list for directory without venvs."""
        result = find_venvs(tmp_path)

        assert result == []

    def test_ignores_directories_without_pyvenv_cfg(self, tmp_path: Path):
        """find_venvs ignores directories that don't contain pyvenv.cfg."""
        fake_venv = tmp_path / ".venv"
        fake_venv.mkdir()

        result = find_venvs(tmp_path)

        assert result == []

    def test_only_checks_common_names(self, tmp_path: Path):
        """find_venvs only checks common venv names, not all directories."""
        custom_venv = tmp_path / "my-custom-venv"
        custom_venv.mkdir()
        (custom_venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        result = find_venvs(tmp_path)

        assert result == []

    def test_checks_all_common_names(self):
        """Common venv names list includes expected entries."""
        assert ".venv" in COMMON_VENV_NAMES
        assert "venv" in COMMON_VENV_NAMES
        assert ".virtualenv" in COMMON_VENV_NAMES
        assert "env" in COMMON_VENV_NAMES
