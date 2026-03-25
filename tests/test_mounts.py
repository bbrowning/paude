"""Tests for volume mount builder."""

from __future__ import annotations

from pathlib import Path

from paude.mounts import build_mounts


class TestBuildMounts:
    """Tests for build_mounts."""

    def test_workspace_is_not_bind_mounted(self, tmp_path: Path):
        """Workspace is NOT bind mounted - uses named volume instead."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        home = tmp_path / "home"
        home.mkdir()

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        # Workspace should NOT be in mounts - it uses a named volume at /pvc
        assert str(workspace) not in mount_str

    def test_gcloud_not_bind_mounted(self, tmp_path: Path):
        """gcloud directory is not bind mounted (uses Podman secrets instead)."""
        home = tmp_path / "home"
        home.mkdir()
        gcloud = home / ".config" / "gcloud"
        gcloud.mkdir(parents=True)

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        assert ".config/gcloud" not in mount_str

    def test_claude_seed_mount_read_only(self, tmp_path: Path):
        """Claude seed mount is read-only when present."""
        home = tmp_path / "home"
        home.mkdir()
        claude = home / ".claude"
        claude.mkdir()

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        assert "/tmp/claude.seed:ro" in mount_str

    def test_plugins_mounted_at_original_path(self, tmp_path: Path):
        """Plugins mounted at original host path."""
        home = tmp_path / "home"
        home.mkdir()
        claude = home / ".claude"
        claude.mkdir()
        plugins = claude / "plugins"
        plugins.mkdir()

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        # Plugins should be mounted at their original path, not /tmp/
        assert str(plugins) in mount_str
        assert f"{plugins}:{plugins}:ro" in mount_str

    def test_gitconfig_mount_read_only(self, tmp_path: Path):
        """gitconfig mount is read-only when present."""
        home = tmp_path / "home"
        home.mkdir()
        gitconfig = home / ".gitconfig"
        gitconfig.write_text("[user]\n  name = Test\n")

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        assert "/home/paude/.gitconfig:ro" in mount_str

    def test_claude_json_mount_read_only(self, tmp_path: Path):
        """claude.json mount is read-only when present."""
        home = tmp_path / "home"
        home.mkdir()
        claude_json = home / ".claude.json"
        claude_json.write_text('{"settings": {}}')

        mounts = build_mounts(home)
        mount_str = " ".join(mounts)

        assert "/tmp/claude.json.seed:ro" in mount_str

    def test_include_config_false_skips_all_config_mounts(self, tmp_path: Path):
        """include_config=False returns no config or gitconfig mounts."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude.json").write_text("{}")
        (home / ".gitconfig").write_text("[user]\n  name = Test\n")

        mounts = build_mounts(home, include_config=False)

        assert mounts == []

    def test_include_config_true_is_default(self, tmp_path: Path):
        """Default behavior includes config mounts."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()

        mounts_default = build_mounts(home)
        mounts_explicit = build_mounts(home, include_config=True)

        assert mounts_default == mounts_explicit
        assert len(mounts_default) > 0
