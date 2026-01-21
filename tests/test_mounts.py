"""Tests for volume mount builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from paude.mounts import build_mounts, build_venv_mounts, get_venv_paths


class TestBuildMounts:
    """Tests for build_mounts."""

    def test_workspace_mount_always_present(self, tmp_path: Path):
        """Workspace mount is always present with rw mode."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        assert str(workspace) in mount_str
        assert ":rw" in mount_str

    def test_gcloud_mount_read_only(self, tmp_path: Path):
        """gcloud mount is read-only when .config/gcloud exists."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        gcloud = home / ".config" / "gcloud"
        gcloud.mkdir(parents=True)

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        assert "/home/paude/.config/gcloud:ro" in mount_str

    def test_gcloud_mount_skipped_when_missing(self, tmp_path: Path):
        """gcloud mount skipped when directory missing."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        # Don't create gcloud dir

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        # Check that .config/gcloud mount is not present (not just "gcloud" substring)
        assert ".config/gcloud" not in mount_str

    def test_claude_seed_mount_read_only(self, tmp_path: Path):
        """Claude seed mount is read-only when present."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        claude = home / ".claude"
        claude.mkdir()

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        assert "/tmp/claude.seed:ro" in mount_str

    def test_plugins_mounted_at_original_path(self, tmp_path: Path):
        """Plugins mounted at original host path."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        claude = home / ".claude"
        claude.mkdir()
        plugins = claude / "plugins"
        plugins.mkdir()

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        # Plugins should be mounted at their original path, not /tmp/
        assert str(plugins) in mount_str
        assert f"{plugins}:{plugins}:ro" in mount_str

    def test_gitconfig_mount_read_only(self, tmp_path: Path):
        """gitconfig mount is read-only when present."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        gitconfig = home / ".gitconfig"
        gitconfig.write_text("[user]\n  name = Test\n")

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        assert "/home/paude/.gitconfig:ro" in mount_str

    def test_claude_json_mount_read_only(self, tmp_path: Path):
        """claude.json mount is read-only when present."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        claude_json = home / ".claude.json"
        claude_json.write_text('{"settings": {}}')

        mounts = build_mounts(workspace, home)
        mount_str = " ".join(mounts)

        assert "/tmp/claude.json.seed:ro" in mount_str


class TestBuildVenvMounts:
    """Tests for build_venv_mounts."""

    def test_returns_empty_when_mode_is_none(self, tmp_path: Path):
        """build_venv_mounts returns empty list when mode is 'none'."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, "none")

        assert mounts == []

    def test_finds_venvs_in_auto_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """build_venv_mounts finds venvs automatically in auto mode."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, "auto")
        mount_str = " ".join(mounts)

        assert "--mount" in mount_str
        assert "type=tmpfs" in mount_str
        assert str(venv) in mount_str

        captured = capsys.readouterr()
        assert "Shadowing venv: .venv" in captured.err

    def test_tmpfs_mount_includes_notmpcopyup(self, tmp_path: Path):
        """build_venv_mounts includes notmpcopyup to prevent copying host contents."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, "auto")
        mount_str = " ".join(mounts)

        assert "notmpcopyup" in mount_str

    def test_tmpfs_mount_is_world_writable(self, tmp_path: Path):
        """build_venv_mounts sets tmpfs-mode=1777 for write access by any user."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, "auto")
        mount_str = " ".join(mounts)

        assert "tmpfs-mode=1777" in mount_str

    def test_returns_empty_when_no_venvs_found(self, tmp_path: Path):
        """build_venv_mounts returns empty list when no venvs found."""
        mounts = build_venv_mounts(tmp_path, "auto")

        assert mounts == []

    def test_uses_manual_list_when_provided(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """build_venv_mounts uses specified directories when mode is list."""
        venv = tmp_path / "my-custom-venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, ["my-custom-venv"])
        mount_str = " ".join(mounts)

        assert str(venv) in mount_str

        captured = capsys.readouterr()
        assert "Shadowing venv: my-custom-venv" in captured.err

    def test_ignores_nonexistent_directories_in_list(self, tmp_path: Path):
        """build_venv_mounts ignores nonexistent directories in manual list."""
        mounts = build_venv_mounts(tmp_path, ["nonexistent-venv"])

        assert mounts == []

    def test_ignores_non_venv_directories_in_list(self, tmp_path: Path):
        """build_venv_mounts ignores directories without pyvenv.cfg in list."""
        not_venv = tmp_path / ".venv"
        not_venv.mkdir()

        mounts = build_venv_mounts(tmp_path, [".venv"])

        assert mounts == []

    def test_finds_multiple_venvs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """build_venv_mounts handles multiple venvs."""
        for name in [".venv", "venv"]:
            venv = tmp_path / name
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        mounts = build_venv_mounts(tmp_path, "auto")

        assert len(mounts) == 4  # 2 venvs * 2 args each (--mount, value)

        captured = capsys.readouterr()
        assert ".venv" in captured.err
        assert "venv" in captured.err

    def test_no_message_when_no_venvs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """build_venv_mounts prints no message when no venvs found."""
        build_venv_mounts(tmp_path, "auto")

        captured = capsys.readouterr()
        assert "Shadowing" not in captured.err

    def test_no_message_when_mode_none(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """build_venv_mounts prints no message when mode is none."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        build_venv_mounts(tmp_path, "none")

        captured = capsys.readouterr()
        assert "Shadowing" not in captured.err


class TestGetVenvPaths:
    """Tests for get_venv_paths."""

    def test_returns_empty_when_mode_is_none(self, tmp_path: Path):
        """get_venv_paths returns empty list when mode is 'none'."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        paths = get_venv_paths(tmp_path, "none")

        assert paths == []

    def test_finds_venvs_in_auto_mode(self, tmp_path: Path):
        """get_venv_paths finds venvs automatically in auto mode."""
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        paths = get_venv_paths(tmp_path, "auto")

        assert len(paths) == 1
        assert paths[0] == venv.resolve()

    def test_returns_empty_when_no_venvs_found(self, tmp_path: Path):
        """get_venv_paths returns empty list when no venvs found."""
        paths = get_venv_paths(tmp_path, "auto")

        assert paths == []

    def test_uses_manual_list_when_provided(self, tmp_path: Path):
        """get_venv_paths uses specified directories when mode is list."""
        venv = tmp_path / "my-custom-venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        paths = get_venv_paths(tmp_path, ["my-custom-venv"])

        assert len(paths) == 1
        assert paths[0] == venv.resolve()

    def test_finds_multiple_venvs(self, tmp_path: Path):
        """get_venv_paths handles multiple venvs."""
        for name in [".venv", "venv"]:
            venv = tmp_path / name
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("home = /usr/bin/python3\n")

        paths = get_venv_paths(tmp_path, "auto")

        assert len(paths) == 2
