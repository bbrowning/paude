"""Tests for the `paude backup` and `paude restore` CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from paude.backends import PodmanBackend, Session
from paude.backup_state import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_FILENAME,
    VOLUME_ARCHIVE_FILENAME,
    BackupManifest,
)
from paude.cli import app

runner = CliRunner()


def _out(result) -> str:
    """Combined stdout + stderr, mirroring the rest of the CLI test suite."""
    return result.stdout + (result.stderr or "")


def _make_session(name: str = "s", status: str = "stopped") -> Session:
    return Session(
        name=name,
        status=status,
        workspace=Path("/some/project"),
        created_at="2026-01-01T00:00:00Z",
        backend_type="podman",
        agent="gemini",
    )


def _mock_backend(
    *, exists: bool = True, running: bool = False, image: str | None = "runtime:img"
) -> PodmanBackend:
    """A real PodmanBackend (so isinstance passes) with a mocked runner."""
    backend = PodmanBackend(engine=MagicMock())
    backend._runner = MagicMock()
    backend._runner.container_exists.return_value = exists
    backend._runner.container_running.return_value = running
    backend._runner.get_container_image.return_value = image
    backend.get_session = MagicMock(return_value=_make_session())  # type: ignore[method-assign]
    return backend


def _gemini_composition():
    from paude.agents import get_agent, get_agent_composition

    return get_agent_composition(get_agent("gemini"))


class TestBackupCommand:
    @pytest.fixture(autouse=True)
    def _xdg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    def _run_backup(self, backend, args, build_manifest=None):
        manifest = build_manifest or BackupManifest(
            name="s",
            workspace="/some/project",
            created_at="2026-08-10T00:00:00+00:00",
            source_paude_version="0.0.0",
        )
        with (
            patch(
                "paude.cli.helpers.find_session_backend",
                return_value=("podman", backend),
            ),
            patch(
                "paude.backends.podman.helpers.get_session_composition",
                return_value=_gemini_composition(),
            ),
            patch("paude.cli.backup._preflight_disk_space"),
            patch(
                "paude.cli.backup._build_manifest", return_value=manifest
            ) as mock_build,
            patch("paude.cli.backup._write_bundle") as mock_write,
        ):
            result = runner.invoke(app, args)
        return result, mock_build, mock_write

    def test_backup_writes_bundle_and_strips_credentials(self, tmp_path: Path) -> None:
        result, _, mock_write = self._run_backup(_mock_backend(), ["backup", "s"])
        assert result.exit_code == 0, _out(result)

        # _write_bundle(backend, vname, image, exclude, manifest, output_path)
        call = mock_write.call_args
        exclude = call.args[3]
        output_path = call.args[5]
        assert ".gemini/oauth_creds.json" in exclude  # always stripped
        assert output_path.parent == tmp_path / "cfg" / "paude" / "backups"
        assert output_path.name.startswith("s-")
        assert output_path.name.endswith(".paude")
        assert "Backed up session 's'" in _out(result)
        assert "re-login" in _out(result)

    def test_backup_auto_selects_when_name_omitted(self) -> None:
        backend = _mock_backend()
        session = _make_session(name="auto")
        manifest = BackupManifest(
            name="auto", workspace="/w", created_at="t", source_paude_version="v"
        )
        with (
            patch(
                "paude.cli.helpers._auto_select_session",
                return_value=(session, backend),
            ),
            patch(
                "paude.backends.podman.helpers.get_session_composition",
                return_value=_gemini_composition(),
            ),
            patch("paude.cli.backup._preflight_disk_space"),
            patch("paude.cli.backup._build_manifest", return_value=manifest),
            patch("paude.cli.backup._write_bundle") as mock_write,
        ):
            result = runner.invoke(app, ["backup"])
        assert result.exit_code == 0, _out(result)
        assert mock_write.call_args.args[5].name.startswith("auto-")

    def test_backup_explicit_backend(self) -> None:
        backend = _mock_backend()
        manifest = BackupManifest(
            name="s", workspace="/w", created_at="t", source_paude_version="v"
        )
        with (
            patch("paude.cli.helpers._get_backend_instance", return_value=backend),
            patch(
                "paude.backends.podman.helpers.get_session_composition",
                return_value=_gemini_composition(),
            ),
            patch("paude.cli.backup._preflight_disk_space"),
            patch("paude.cli.backup._build_manifest", return_value=manifest),
            patch("paude.cli.backup._write_bundle") as mock_write,
        ):
            result = runner.invoke(app, ["backup", "s", "--backend", "podman"])
        assert result.exit_code == 0, _out(result)
        mock_write.assert_called_once()

    def test_backup_blocks_on_low_disk_space(self) -> None:
        backend = _mock_backend()
        with (
            patch(
                "paude.cli.helpers.find_session_backend",
                return_value=("podman", backend),
            ),
            patch(
                "paude.backends.podman.volume_archive.VolumeArchiver.volume_size_bytes",
                return_value=100 * 1024**3,
            ),
            patch(
                "paude.cli.backup.shutil.disk_usage",
                return_value=MagicMock(free=1024**3),
            ),
            patch("paude.cli.backup._write_bundle") as mock_write,
        ):
            result = runner.invoke(app, ["backup", "s"])
        assert result.exit_code == 1
        assert "free space" in _out(result)
        mock_write.assert_not_called()

    def test_backup_force_bypasses_low_disk_space(self) -> None:
        backend = _mock_backend()
        manifest = BackupManifest(
            name="s", workspace="/w", created_at="t", source_paude_version="v"
        )
        with (
            patch(
                "paude.cli.helpers.find_session_backend",
                return_value=("podman", backend),
            ),
            patch(
                "paude.backends.podman.helpers.get_session_composition",
                return_value=_gemini_composition(),
            ),
            patch(
                "paude.backends.podman.volume_archive.VolumeArchiver.volume_size_bytes",
                return_value=100 * 1024**3,
            ),
            patch(
                "paude.cli.backup.shutil.disk_usage",
                return_value=MagicMock(free=1024**3),
            ),
            patch("paude.cli.backup._build_manifest", return_value=manifest),
            patch("paude.cli.backup._write_bundle") as mock_write,
        ):
            result = runner.invoke(app, ["backup", "s", "--force"])
        assert result.exit_code == 0, _out(result)
        mock_write.assert_called_once()

    def test_backup_refuses_running_session(self) -> None:
        result, _, mock_write = self._run_backup(
            _mock_backend(running=True), ["backup", "s"]
        )
        assert result.exit_code == 1
        assert "is running" in _out(result)
        assert "paude stop s" in _out(result)
        mock_write.assert_not_called()

    def test_backup_session_not_found_in_registry(self) -> None:
        with patch("paude.cli.helpers.find_session_backend", return_value=None):
            result = runner.invoke(app, ["backup", "s"])
        assert result.exit_code == 1
        assert "not found" in _out(result)

    def test_backup_missing_container(self) -> None:
        result, _, mock_write = self._run_backup(
            _mock_backend(exists=False), ["backup", "s"]
        )
        assert result.exit_code == 1
        assert "not found" in _out(result)
        mock_write.assert_not_called()

    def test_backup_missing_image(self) -> None:
        result, _, mock_write = self._run_backup(
            _mock_backend(image=None), ["backup", "s"]
        )
        assert result.exit_code == 1
        assert "image" in _out(result)
        mock_write.assert_not_called()

    def test_backup_output_directory_gets_default_name(self, tmp_path: Path) -> None:
        dest = tmp_path / "dest"
        dest.mkdir()
        result, _, mock_write = self._run_backup(
            _mock_backend(), ["backup", "s", "-o", str(dest)]
        )
        assert result.exit_code == 0, _out(result)
        output_path = mock_write.call_args.args[5]
        assert output_path.parent == dest
        assert output_path.name.startswith("s-")

    def test_backup_output_explicit_file(self, tmp_path: Path) -> None:
        target = tmp_path / "mine.paude.tar"
        result, _, mock_write = self._run_backup(
            _mock_backend(), ["backup", "s", "--output", str(target)]
        )
        assert result.exit_code == 0, _out(result)
        assert mock_write.call_args.args[5] == target


class TestRestoreCommand:
    def _make_bundle(
        self, tmp_path: Path, manifest: BackupManifest, *, name: str = "b.paude"
    ) -> Path:
        bundle = tmp_path / name
        bundle.mkdir()
        (bundle / MANIFEST_FILENAME).write_text(manifest.to_json())
        (bundle / VOLUME_ARCHIVE_FILENAME).write_bytes(b"not-a-real-gzip")
        return bundle

    def _manifest(self) -> BackupManifest:
        return BackupManifest(
            name="orig",
            workspace="/w",
            created_at="t",
            source_paude_version="0.0.0",
            agent="claude",
            engine="podman",
        )

    def test_restore_dry_run_reports_plan_and_exits_2(self, tmp_path: Path) -> None:
        bundle = self._make_bundle(tmp_path, self._manifest())
        result = runner.invoke(app, ["restore", str(bundle)])
        assert result.exit_code == 2
        out = _out(result)
        assert "not yet implemented" in out
        assert "orig" in out  # session name from manifest
        assert "podman" in out

    def test_restore_name_override(self, tmp_path: Path) -> None:
        bundle = self._make_bundle(tmp_path, self._manifest())
        result = runner.invoke(app, ["restore", str(bundle), "--name", "renamed"])
        assert result.exit_code == 2
        assert "renamed" in _out(result)

    def test_restore_missing_bundle(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["restore", str(tmp_path / "nope.paude")])
        assert result.exit_code == 1
        assert "not found" in _out(result)

    def test_restore_bundle_without_manifest(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bad.paude"
        bundle.mkdir()
        (bundle / VOLUME_ARCHIVE_FILENAME).write_bytes(b"x")
        result = runner.invoke(app, ["restore", str(bundle)])
        assert result.exit_code == 1
        assert "manifest" in _out(result)

    def test_restore_wrong_format_version(self, tmp_path: Path) -> None:
        manifest = self._manifest()
        manifest.backup_format_version = BACKUP_FORMAT_VERSION + 1
        bundle = self._make_bundle(tmp_path, manifest)
        result = runner.invoke(app, ["restore", str(bundle)])
        assert result.exit_code == 1
        assert "Unsupported backup format" in _out(result)


class TestBuildManifest:
    def test_maps_labels_and_registry_fields(self) -> None:
        from paude.backends.labels import (
            PAUDE_LABEL_DOMAINS,
            PAUDE_LABEL_GPU,
            PAUDE_LABEL_OTEL_ENDPOINT,
            PAUDE_LABEL_PROXY_IMAGE,
            PAUDE_LABEL_YOLO,
        )
        from paude.cli.backup import _build_manifest
        from paude.registry import RegistryEntry

        backend = PodmanBackend(engine=MagicMock())
        backend._runner = MagicMock()
        session = Session(
            name="s",
            status="stopped",
            workspace=Path("/w"),
            created_at="c",
            backend_type="podman",
            agent="claude",
            provider="vertex",
            agent_providers=[("claude", "vertex")],
            credential_providers=["vertex"],
        )
        labels = {
            PAUDE_LABEL_GPU: "all",
            PAUDE_LABEL_YOLO: "1",
            PAUDE_LABEL_DOMAINS: "a,b",
            PAUDE_LABEL_OTEL_ENDPOINT: "http://collector:4318",
            PAUDE_LABEL_PROXY_IMAGE: "proxy:1",
        }
        entry = RegistryEntry(
            name="s",
            backend_type="podman",
            workspace="/w",
            agent="claude",
            created_at="c",
            engine="docker",
            ssh_host="user@box",
            ssh_key="/k",
            remote_config_dir="/tmp/cfg",
        )
        from datetime import UTC, datetime

        now = datetime(2026, 8, 10, tzinfo=UTC)
        with (
            patch(
                "paude.backends.podman.helpers.find_container_by_session_name",
                return_value={"Labels": labels},
            ),
            patch(
                "paude.backends.podman.helpers.build_session_from_container",
                return_value=session,
            ),
            patch("paude.registry.SessionRegistry") as mock_reg,
        ):
            mock_reg.return_value.get.return_value = entry
            manifest = _build_manifest(backend, "s", "runtime:img", now)

        assert manifest.gpu == "all"
        assert manifest.yolo is True
        assert manifest.allowed_domains == ["a", "b"]
        assert manifest.otel_endpoint == "http://collector:4318"
        assert manifest.proxy_image == "proxy:1"
        assert manifest.image == "runtime:img"
        assert manifest.agent_providers == [("claude", "vertex")]
        # Registry-only fields the labels don't carry.
        assert manifest.engine == "docker"
        assert manifest.ssh_host == "user@box"
        assert manifest.remote_config_dir == "/tmp/cfg"

    def test_absent_domains_label_means_none(self) -> None:
        from paude.cli.backup import _build_manifest

        backend = PodmanBackend(engine=MagicMock())
        backend._runner = MagicMock()
        from datetime import UTC, datetime

        with (
            patch(
                "paude.backends.podman.helpers.find_container_by_session_name",
                return_value={"Labels": {}},
            ),
            patch(
                "paude.backends.podman.helpers.build_session_from_container",
                return_value=_make_session(),
            ),
            patch("paude.registry.SessionRegistry") as mock_reg,
        ):
            mock_reg.return_value.get.return_value = None
            manifest = _build_manifest(
                backend, "s", "img", datetime(2026, 8, 10, tzinfo=UTC)
            )

        assert manifest.allowed_domains is None
        assert manifest.gpu is None
        assert manifest.yolo is False
        # No registry entry: engine falls back to the session's backend type.
        assert manifest.engine == "podman"


class TestWriteBundle:
    def test_assembles_atomic_directory_bundle(self, tmp_path: Path) -> None:
        from paude.cli.backup import _write_bundle

        manifest = BackupManifest(
            name="s",
            workspace="/w",
            created_at="t",
            source_paude_version="v",
        )
        output = tmp_path / "s.paude"

        def fake_export(vol, image, local, exclude=None, progress=None):
            Path(local).write_bytes(b"PVCDATA")
            return "deadbeef"

        archiver = MagicMock()
        archiver.export_volume.side_effect = fake_export
        _write_bundle(archiver, "vol", "img", [], manifest, output)

        assert output.is_dir()
        assert (output.stat().st_mode & 0o777) == 0o700
        pvc = output / VOLUME_ARCHIVE_FILENAME
        man = output / MANIFEST_FILENAME
        assert pvc.is_file()
        assert man.is_file()
        assert (pvc.stat().st_mode & 0o777) == 0o600
        assert (man.stat().st_mode & 0o777) == 0o600
        # The archive checksum comes from export_volume (computed in-container).
        assert manifest.archive_sha256 == "deadbeef"
        # No staging directory is left behind.
        assert list(tmp_path.glob(".paude-backup-*")) == []


class TestArchiveProgress:
    def test_noop_when_not_a_tty(self, capsys) -> None:
        from paude.cli.backup import _ArchiveProgress

        progress = _ArchiveProgress()
        # Under pytest capture stderr is not a TTY, so nothing renders.
        assert progress._enabled is False
        progress.update(1024 * 1024)
        progress.finish()
        assert capsys.readouterr().err == ""

    def test_renders_throughput_and_throttles(self, capsys) -> None:
        from paude.cli.backup import _ArchiveProgress

        progress = _ArchiveProgress()
        progress._enabled = True  # force rendering off a TTY for the test

        progress.update(2 * 1024 * 1024)
        first = capsys.readouterr().err
        assert "2.0MB written" in first
        assert "/s" in first  # a throughput figure is shown

        # A second update within the redraw interval is throttled.
        progress.update(3 * 1024 * 1024)
        assert capsys.readouterr().err == ""

        # finish() ends the in-place line so later output starts fresh.
        progress.finish()
        assert capsys.readouterr().err == "\n"


class TestPreflightDiskSpace:
    def test_blocks_when_space_short(self, tmp_path: Path) -> None:
        from paude.cli.backup import _preflight_disk_space

        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = 100 * 1024**3  # 100 GB
        usage = MagicMock(free=1 * 1024**3)  # 1 GB free
        with (
            patch("paude.cli.backup.shutil.disk_usage", return_value=usage),
            pytest.raises(typer.Exit),
        ):
            _preflight_disk_space(archiver, "vol", "img", tmp_path, force=False)

    def test_force_skips_check(self, tmp_path: Path) -> None:
        from paude.cli.backup import _preflight_disk_space

        archiver = MagicMock()
        _preflight_disk_space(archiver, "vol", "img", tmp_path, force=True)
        archiver.volume_size_bytes.assert_not_called()

    def test_unknown_size_is_noop(self, tmp_path: Path) -> None:
        from paude.cli.backup import _preflight_disk_space

        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = None
        # Should not raise even though we never check disk_usage.
        _preflight_disk_space(archiver, "vol", "img", tmp_path, force=False)

    def test_enough_space_passes(self, tmp_path: Path) -> None:
        from paude.cli.backup import _preflight_disk_space

        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = 1 * 1024**3
        usage = MagicMock(free=100 * 1024**3)
        with patch("paude.cli.backup.shutil.disk_usage", return_value=usage):
            _preflight_disk_space(archiver, "vol", "img", tmp_path, force=False)


class TestBackupHelp:
    def test_backup_appears_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "backup" in result.stdout

    def test_backup_command_help(self) -> None:
        result = runner.invoke(app, ["backup", "--help"])
        assert result.exit_code == 0
        assert "bundle" in result.stdout.lower()

    def test_restore_command_help(self) -> None:
        result = runner.invoke(app, ["restore", "--help"])
        assert result.exit_code == 0
        assert "bundle" in result.stdout.lower()
