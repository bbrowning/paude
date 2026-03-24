"""Tests for remote config sync."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.cli.commands import _cleanup_remote_config_dir
from paude.registry import RegistryEntry
from paude.transport.config_sync import (
    _parse_mount_sources,
    _transfer_path,
    cleanup_remote_configs,
    remap_mounts,
    sync_configs_to_remote,
)


class TestParseMountSources:
    def test_extracts_sources_from_v_args(self) -> None:
        mounts = ["-v", "/home/user/.gitconfig:/home/paude/.gitconfig:ro"]
        assert _parse_mount_sources(mounts) == ["/home/user/.gitconfig"]

    def test_multiple_mounts(self) -> None:
        mounts = [
            "-v",
            "/a:/b:ro",
            "-v",
            "/c:/d",
        ]
        assert _parse_mount_sources(mounts) == ["/a", "/c"]

    def test_skips_named_volumes(self) -> None:
        mounts = ["-v", "my-volume:/pvc"]
        assert _parse_mount_sources(mounts) == []

    def test_empty_mounts(self) -> None:
        assert _parse_mount_sources([]) == []

    def test_mixed_flags_and_mounts(self) -> None:
        mounts = ["-e", "FOO=bar", "-v", "/src:/dst"]
        assert _parse_mount_sources(mounts) == ["/src"]


class TestRemapMounts:
    def test_replaces_matching_sources(self) -> None:
        mounts = ["-v", "/local/file:/container/file:ro"]
        path_map = {"/local/file": "/tmp/paude-config-XXXX/0/file"}
        result = remap_mounts(mounts, path_map)
        assert result == ["-v", "/tmp/paude-config-XXXX/0/file:/container/file:ro"]

    def test_leaves_unmatched_mounts(self) -> None:
        mounts = ["-v", "/other:/dst"]
        path_map = {"/local/file": "/remote/file"}
        result = remap_mounts(mounts, path_map)
        assert result == ["-v", "/other:/dst"]

    def test_preserves_non_mount_args(self) -> None:
        mounts = ["-e", "FOO=bar", "-v", "/a:/b"]
        path_map = {"/a": "/remote/a"}
        result = remap_mounts(mounts, path_map)
        assert result == ["-e", "FOO=bar", "-v", "/remote/a:/b"]

    def test_empty_path_map(self) -> None:
        mounts = ["-v", "/a:/b"]
        result = remap_mounts(mounts, {})
        assert result == ["-v", "/a:/b"]

    def test_named_volumes_pass_through(self) -> None:
        mounts = ["-v", "myvol:/pvc"]
        path_map = {"/some/path": "/remote/path"}
        result = remap_mounts(mounts, path_map)
        assert result == ["-v", "myvol:/pvc"]


class TestTransferPath:
    @patch("paude.transport.config_sync.subprocess.Popen")
    @patch("paude.transport.config_sync.is_macos", return_value=True)
    def test_macos_includes_no_mac_metadata(self, mock_macos, mock_popen, tmp_path):
        transport = MagicMock()
        transport.ssh_base.return_value = ["ssh", "host"]

        local_dir = tmp_path / "mydir"
        local_dir.mkdir()
        (local_dir / "file.txt").write_text("hello")

        mock_tar = MagicMock()
        mock_tar.stdout = MagicMock()
        mock_untar = MagicMock()
        mock_untar.returncode = 0
        mock_tar.returncode = 0
        mock_popen.side_effect = [mock_tar, mock_untar]

        _transfer_path(transport, str(local_dir), "/remote/mydir")

        mock_macos.assert_called_once()
        tar_cmd = mock_popen.call_args_list[0][0][0]
        assert tar_cmd[0] == "tar"
        assert "--no-mac-metadata" in tar_cmd
        assert "-cf" in tar_cmd

    @patch("paude.transport.config_sync.subprocess.Popen")
    @patch("paude.transport.config_sync.is_macos", return_value=False)
    def test_non_macos_excludes_no_mac_metadata(self, mock_macos, mock_popen, tmp_path):
        transport = MagicMock()
        transport.ssh_base.return_value = ["ssh", "host"]

        local_dir = tmp_path / "mydir"
        local_dir.mkdir()
        (local_dir / "file.txt").write_text("hello")

        mock_tar = MagicMock()
        mock_tar.stdout = MagicMock()
        mock_untar = MagicMock()
        mock_untar.returncode = 0
        mock_tar.returncode = 0
        mock_popen.side_effect = [mock_tar, mock_untar]

        _transfer_path(transport, str(local_dir), "/remote/mydir")

        mock_macos.assert_called_once()
        tar_cmd = mock_popen.call_args_list[0][0][0]
        assert tar_cmd[0] == "tar"
        assert "--no-mac-metadata" not in tar_cmd


class TestSyncConfigsToRemote:
    @patch("paude.transport.config_sync._transfer_path")
    def test_creates_temp_dir_and_transfers(self, mock_transfer, tmp_path) -> None:
        transport = MagicMock()
        transport.run.return_value = MagicMock(
            returncode=0, stdout="/tmp/paude-config-XXXX\n"
        )
        mock_transfer.return_value = True

        # Create a local file to reference
        local_file = tmp_path / ".gitconfig"
        local_file.write_text("[user]\nname = Test")

        mounts = ["-v", f"{local_file}:/home/paude/.gitconfig:ro"]
        result = sync_configs_to_remote(transport, mounts)

        assert result.remote_base == "/tmp/paude-config-XXXX"
        assert str(local_file) in result.path_map
        mock_transfer.assert_called_once()

    def test_skips_nonexistent_local_files(self) -> None:
        transport = MagicMock()
        transport.run.return_value = MagicMock(
            returncode=0, stdout="/tmp/paude-config-XXXX\n"
        )

        mounts = ["-v", "/nonexistent/file:/dst:ro"]
        result = sync_configs_to_remote(transport, mounts)

        assert result.path_map == {}


class TestCleanupRemoteConfigs:
    def test_removes_temp_dir(self) -> None:
        transport = MagicMock()
        cleanup_remote_configs(transport, "/tmp/paude-config-XXXX")
        transport.run.assert_called_once_with(
            ["rm", "-rf", "/tmp/paude-config-XXXX"], check=False
        )

    def test_rejects_suspicious_paths(self) -> None:
        transport = MagicMock()
        cleanup_remote_configs(transport, "/home/user")
        transport.run.assert_not_called()

    def test_rejects_empty_path(self) -> None:
        transport = MagicMock()
        cleanup_remote_configs(transport, "")
        transport.run.assert_not_called()


def _make_entry(**kwargs: object) -> RegistryEntry:
    defaults: dict[str, object] = {
        "name": "test",
        "backend_type": "docker",
        "workspace": "/home/user/test",
        "agent": "claude",
        "created_at": "2026-03-23T10:00:00Z",
    }
    defaults.update(kwargs)
    return RegistryEntry(**defaults)  # type: ignore[arg-type]


class TestCleanupRemoteConfigDir:
    """Tests for _cleanup_remote_config_dir helper in commands.py."""

    @patch("paude.transport.config_sync.cleanup_remote_configs")
    @patch("paude.cli.remote_git_setup._build_transport")
    def test_calls_cleanup_when_entry_has_remote_config(
        self, mock_build, mock_cleanup
    ) -> None:
        mock_transport = MagicMock()
        mock_build.return_value = mock_transport
        entry = _make_entry(
            ssh_host="myhost",
            ssh_key="/path/key",
            remote_config_dir="/tmp/paude-config-XXXX",
        )
        _cleanup_remote_config_dir(entry)

        mock_build.assert_called_once_with("myhost", "/path/key")
        mock_cleanup.assert_called_once_with(mock_transport, "/tmp/paude-config-XXXX")

    def test_noop_when_entry_is_none(self) -> None:
        _cleanup_remote_config_dir(None)  # should not raise

    def test_noop_when_no_remote_config_dir(self) -> None:
        entry = _make_entry(ssh_host="myhost")
        _cleanup_remote_config_dir(entry)  # should not raise

    def test_noop_when_no_ssh_host(self) -> None:
        entry = _make_entry(remote_config_dir="/tmp/paude-config-XXXX")
        _cleanup_remote_config_dir(entry)  # should not raise

    @patch(
        "paude.cli.remote_git_setup._build_transport",
        side_effect=Exception("connection failed"),
    )
    def test_swallows_exceptions(self, mock_build) -> None:
        entry = _make_entry(
            ssh_host="myhost",
            remote_config_dir="/tmp/paude-config-XXXX",
        )
        _cleanup_remote_config_dir(entry)  # should not raise
