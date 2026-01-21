"""Tests for volume mount builder."""

from __future__ import annotations

from pathlib import Path

from paude.mounts import build_mounts


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
