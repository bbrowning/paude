"""Tests for the `paude backup` and `paude restore` CLI commands."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from click.testing import Result
from typer.testing import CliRunner

from paude.backends import PodmanBackend, Session
from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_AGENT_PROVIDERS,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_ENDPOINTS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_PROVIDER,
    PAUDE_LABEL_PROVIDERS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
    LabeledSession,
    encode_agent_providers,
    encode_providers,
    read_labels,
)
from paude.backends.session_env import encode_path
from paude.backup_state import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_FILENAME,
    VOLUME_ARCHIVE_FILENAME,
    BackupManifest,
)
from paude.cli import app
from paude.transport.ssh import SshTransport
from tests.fakes import (
    FakeTransport,
    assert_carries_every_spec_field,
    make_backend,
    make_engine,
    make_runner,
)

runner = CliRunner()


def _out(result: Result) -> str:
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


def _session_container(name: str = "s", agent: str = "gemini") -> dict[str, object]:
    """A container dict find_container_by_session_name will match."""
    return {
        "Id": "abc123",
        "Labels": {
            PAUDE_LABEL_SESSION: name,
            PAUDE_LABEL_AGENT: agent,
            PAUDE_LABEL_WORKSPACE: encode_path(Path("/some/project"), url_safe=True),
            PAUDE_LABEL_CREATED: "2026-01-01T00:00:00Z",
        },
    }


def _mock_backend(
    *,
    name: str = "s",
    exists: bool = True,
    running: bool = False,
    image: str | None = "runtime:img",
    remote: bool = False,
) -> PodmanBackend:
    """A real PodmanBackend (so isinstance passes) with a mocked runner.

    ``remote=True`` wires ``_engine`` to look like an SSH-backed session:
    ``is_remote`` is true and ``transport`` is a real (but unconnected)
    ``SshTransport`` so ``isinstance`` checks in the ``--remote-only`` path
    pass. Callers that exercise that path still need to mock/patch the
    transport's ``run`` for whichever calls they don't stub out at a higher
    level.
    """
    transport = SshTransport("user@host") if remote else FakeTransport()
    runner = make_runner(
        make_engine(transport=transport, is_remote=remote),
        container_exists=exists,
        container_running=running,
        get_container_image=image,
        # Backup reads the session's labels off its container, so the double
        # has to have one to find.
        list_containers=[_session_container(name)],
    )
    backend = make_backend(runner)
    backend.get_session = MagicMock(return_value=_make_session())  # type: ignore[method-assign]
    return backend


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
        backend = _mock_backend(name="auto")
        session = _make_session(name="auto")
        manifest = BackupManifest(
            name="auto", workspace="/w", created_at="t", source_paude_version="v"
        )
        with (
            patch(
                "paude.cli.helpers._auto_select_session",
                return_value=(session, backend),
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


class TestRemoteOnlyBackupCommand:
    def _run(
        self,
        backend,
        args,
        *,
        exists_remote: bool = False,
        build_manifest: BackupManifest | None = None,
    ):
        manifest = build_manifest or BackupManifest(
            name="s",
            workspace="/some/project",
            created_at="2026-08-10T00:00:00+00:00",
            source_paude_version="0.0.0",
        )
        transport = backend._engine.transport
        exists_rc = 0 if exists_remote else 1
        with (
            patch(
                "paude.cli.helpers.find_session_backend",
                return_value=("podman", backend),
            ),
            patch.object(
                transport,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=exists_rc, stdout="", stderr=""
                ),
            ),
            patch(
                "paude.cli.backup._resolve_remote_output_path",
                return_value="/home/user/.config/paude/backups/s-2026.paude",
            ) as mock_resolve,
            patch("paude.cli.backup._preflight_disk_space_remote"),
            patch(
                "paude.cli.backup._build_manifest", return_value=manifest
            ) as mock_build,
            patch("paude.cli.backup._write_bundle_remote") as mock_write,
        ):
            result = runner.invoke(app, args)
        return result, mock_resolve, mock_build, mock_write

    def test_rejects_local_session(self) -> None:
        backend = _mock_backend(remote=False)
        result, _, _, mock_write = self._run(backend, ["backup", "s", "--remote-only"])
        assert result.exit_code == 1
        assert "does not run on a remote host" in _out(result)
        mock_write.assert_not_called()

    def test_writes_bundle_on_remote_host(self) -> None:
        backend = _mock_backend(remote=True)
        result, mock_resolve, _, mock_write = self._run(
            backend, ["backup", "s", "--remote-only"]
        )
        assert result.exit_code == 0, _out(result)

        # _resolve_remote_output_path(transport, output, name, now)
        assert mock_resolve.call_args.args[0] is backend._engine.transport
        assert mock_resolve.call_args.args[1] is None
        assert mock_resolve.call_args.args[2] == "s"

        # _write_bundle_remote(archiver, transport, vname, image, exclude, manifest, path)
        call = mock_write.call_args
        assert call.args[1] is backend._engine.transport
        assert call.args[6] == "/home/user/.config/paude/backups/s-2026.paude"

        assert (
            "Backed up session 's' to "
            "user@host:/home/user/.config/paude/backups/s-2026.paude" in _out(result)
        )

    def test_preflight_receives_parent_directory_not_bundle_path(self) -> None:
        """Regression: the preflight must check the bundle's parent dir.

        Passing the not-yet-existing bundle *file* path instead would make
        `df` always fail against a nonexistent path, silently disabling the
        free-space check on every --remote-only backup.
        """
        backend = _mock_backend(remote=True)
        manifest = BackupManifest(
            name="s",
            workspace="/some/project",
            created_at="t",
            source_paude_version="v",
        )
        transport = backend._engine.transport
        with (
            patch(
                "paude.cli.helpers.find_session_backend",
                return_value=("podman", backend),
            ),
            patch.object(
                transport,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                ),
            ),
            patch(
                "paude.cli.backup._resolve_remote_output_path",
                return_value="/home/user/.config/paude/backups/s-2026.paude",
            ),
            patch("paude.cli.backup._preflight_disk_space_remote") as mock_preflight,
            patch("paude.cli.backup._build_manifest", return_value=manifest),
            patch("paude.cli.backup._write_bundle_remote"),
        ):
            result = runner.invoke(app, ["backup", "s", "--remote-only"])

        assert result.exit_code == 0, _out(result)
        assert mock_preflight.call_args.args[4] == "/home/user/.config/paude/backups"

    def test_destination_already_exists_on_remote(self) -> None:
        backend = _mock_backend(remote=True)
        result, _, _, mock_write = self._run(
            backend, ["backup", "s", "--remote-only"], exists_remote=True
        )
        assert result.exit_code == 1
        assert "already exists" in _out(result)
        mock_write.assert_not_called()

    def test_passes_output_through_to_remote_resolver(self) -> None:
        backend = _mock_backend(remote=True)
        _, mock_resolve, _, _ = self._run(
            backend, ["backup", "s", "--remote-only", "-o", "/srv/backups"]
        )
        assert mock_resolve.call_args.args[1] == "/srv/backups"


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
    """The manifest is assembled from one label read plus the registry entry."""

    def _view(self, **labels: str) -> LabeledSession:
        return read_labels(
            {
                PAUDE_LABEL_SESSION: "s",
                PAUDE_LABEL_AGENT: "claude",
                PAUDE_LABEL_PROVIDER: "vertex",
                PAUDE_LABEL_AGENT_PROVIDERS: encode_agent_providers(
                    [("claude", "vertex")]
                ),
                PAUDE_LABEL_PROVIDERS: encode_providers(["vertex"]),
                PAUDE_LABEL_WORKSPACE: encode_path(Path("/w"), url_safe=True),
                PAUDE_LABEL_CREATED: "c",
                **labels,
            }
        )

    def test_captures_labels_and_registry_fields(self) -> None:
        from paude.cli.backup import _build_manifest
        from paude.registry import RegistryEntry

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
        view = self._view(
            **{
                PAUDE_LABEL_GPU: "all",
                PAUDE_LABEL_YOLO: "1",
                PAUDE_LABEL_DOMAINS: "a,b",
                PAUDE_LABEL_ENDPOINTS: "api.example.com:8443",
                PAUDE_LABEL_OTEL_ENDPOINT: "http://collector:4318",
                PAUDE_LABEL_PROXY_IMAGE: "proxy:1",
            }
        )
        now = datetime(2026, 8, 10, tzinfo=UTC)

        with patch("paude.registry.SessionRegistry") as mock_reg:
            mock_reg.return_value.get.return_value = entry
            manifest = _build_manifest(view, "s", "runtime:img", now, "podman")

        assert manifest.gpu == "all"
        assert manifest.yolo is True
        assert manifest.allowed_domains == ["a", "b"]
        assert manifest.otel_endpoint == "http://collector:4318"
        assert manifest.proxy_image == "proxy:1"
        assert manifest.image == "runtime:img"
        assert manifest.agent_providers == [("claude", "vertex")]
        assert manifest.workspace == "/w"
        assert manifest.session_created_at == "c"
        # Registry-only fields the labels don't carry.
        assert manifest.engine == "docker"
        assert manifest.ssh_host == "user@box"
        assert manifest.remote_config_dir == "/tmp/cfg"

    def test_absent_domains_label_means_none(self) -> None:
        from paude.cli.backup import _build_manifest

        with patch("paude.registry.SessionRegistry") as mock_reg:
            mock_reg.return_value.get.return_value = None
            manifest = _build_manifest(
                read_labels({}), "s", "img", datetime(2026, 8, 10, tzinfo=UTC), "podman"
            )

        assert manifest.allowed_domains is None
        assert manifest.gpu is None
        assert manifest.yolo is False
        # No registry entry: engine falls back to the backend type.
        assert manifest.engine == "podman"
        # No workspace label: the placeholder, not a crash.
        assert manifest.workspace == "/"

    def test_every_spec_field_reaches_the_manifest(self) -> None:
        """The BackupManifest half of the REFACTOR-007 drift guard.

        Inheriting SessionSpec dedupes the schema, not the copying: a tenth
        label would persist as its default until _build_manifest carried it.
        """
        from paude.cli.backup import _build_manifest

        view = self._view(
            **{
                PAUDE_LABEL_AGENT: "codex",
                PAUDE_LABEL_GPU: "all",
                PAUDE_LABEL_YOLO: "1",
                PAUDE_LABEL_DOMAINS: "a,b",
                PAUDE_LABEL_ENDPOINTS: "api.example.com:8443",
                PAUDE_LABEL_OTEL_ENDPOINT: "http://collector:4318",
                PAUDE_LABEL_PROXY_IMAGE: "proxy:1",
            }
        )

        with patch("paude.registry.SessionRegistry") as mock_reg:
            mock_reg.return_value.get.return_value = None
            manifest = _build_manifest(
                view, "s", "img", datetime(2026, 8, 10, tzinfo=UTC), "podman"
            )

        assert_carries_every_spec_field(manifest, "_build_manifest")


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


class TestResolveRemoteOutputPath:
    def test_default_dir_resolved_via_ssh_and_mkdir_p(self) -> None:
        from datetime import UTC, datetime

        from paude.cli.backup import _resolve_remote_output_path

        transport = SshTransport("user@host")
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "sh":
                return subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="/home/u/.config/paude/backups",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        with patch.object(transport, "run", side_effect=fake_run):
            path = _resolve_remote_output_path(
                transport, None, "s", datetime(2026, 8, 10, tzinfo=UTC)
            )

        assert path == "/home/u/.config/paude/backups/s-20260810T000000Z.paude"
        # Resolving the default dir and creating it are combined into a
        # single round trip -- each SSH connection has a real cost on the
        # slow link this feature targets.
        assert len(calls) == 1
        assert calls[0][0] == "sh"
        assert "mkdir -p" in calls[0][2]

    def test_existing_directory_gets_default_name(self) -> None:
        from datetime import UTC, datetime

        from paude.cli.backup import _resolve_remote_output_path

        transport = SshTransport("user@host")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "test":
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        with patch.object(transport, "run", side_effect=fake_run):
            path = _resolve_remote_output_path(
                transport, "/srv/backups", "s", datetime(2026, 8, 10, tzinfo=UTC)
            )
        assert path == "/srv/backups/s-20260810T000000Z.paude"

    def test_explicit_non_directory_path_used_verbatim(self) -> None:
        from datetime import UTC, datetime

        from paude.cli.backup import _resolve_remote_output_path

        transport = SshTransport("user@host")

        def fake_run(cmd, **kwargs):
            if cmd[0] == "test":
                return subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr=""
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        with patch.object(transport, "run", side_effect=fake_run):
            path = _resolve_remote_output_path(
                transport, "/srv/mine.paude", "s", datetime(2026, 8, 10, tzinfo=UTC)
            )
        assert path == "/srv/mine.paude"


class TestPreflightDiskSpaceRemote:
    _DF_SHORT = (
        "Filesystem     1024-blocks      Used   Available Capacity Mounted on\n"
        "/dev/sda1        104857600 100000000     1048576      99% /\n"
    )
    _DF_ENOUGH = (
        "Filesystem     1024-blocks    Used    Available Capacity Mounted on\n"
        "/dev/sda1       1073741824   10000   1000000000       1% /\n"
    )

    def test_blocks_when_space_short(self) -> None:
        from paude.cli.backup import _preflight_disk_space_remote

        transport = SshTransport("user@host")
        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = 100 * 1024**3
        with (
            patch.object(
                transport,
                "run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=self._DF_SHORT, stderr=""
                ),
            ),
            pytest.raises(typer.Exit),
        ):
            _preflight_disk_space_remote(
                archiver, "vol", "img", transport, "/remote/dir", force=False
            )

    def test_force_skips_check(self) -> None:
        from paude.cli.backup import _preflight_disk_space_remote

        transport = SshTransport("user@host")
        archiver = MagicMock()
        _preflight_disk_space_remote(
            archiver, "vol", "img", transport, "/remote/dir", force=True
        )
        archiver.volume_size_bytes.assert_not_called()

    def test_unknown_remote_free_space_is_noop(self) -> None:
        from paude.cli.backup import _preflight_disk_space_remote

        transport = SshTransport("user@host")
        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = 1024
        with patch.object(
            transport,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="df: not found"
            ),
        ):
            _preflight_disk_space_remote(
                archiver, "vol", "img", transport, "/remote/dir", force=False
            )

    def test_enough_space_passes(self) -> None:
        from paude.cli.backup import _preflight_disk_space_remote

        transport = SshTransport("user@host")
        archiver = MagicMock()
        archiver.volume_size_bytes.return_value = 1 * 1024**3
        with patch.object(
            transport,
            "run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=self._DF_ENOUGH, stderr=""
            ),
        ):
            _preflight_disk_space_remote(
                archiver, "vol", "img", transport, "/remote/dir", force=False
            )


class TestWriteBundleRemote:
    def test_assembles_bundle_on_remote_host(self) -> None:
        from paude.cli.backup import _write_bundle_remote

        manifest = BackupManifest(
            name="s", workspace="/w", created_at="t", source_paude_version="v"
        )
        transport = SshTransport("user@host")
        calls: list[list[str]] = []
        tmp_dir = "/home/u/backups/.paude-backup-abcd"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "mktemp":
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=f"{tmp_dir}\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        archiver = MagicMock()
        archiver.export_volume_to_remote_file.return_value = "deadbeef"

        with patch.object(transport, "run", side_effect=fake_run):
            _write_bundle_remote(
                archiver,
                transport,
                "vol",
                "img",
                [],
                manifest,
                "/home/u/backups/s.paude",
            )

        assert manifest.archive_sha256 == "deadbeef"
        # The archive is written directly into the remote temp dir -- no local
        # staging, no second copy.
        archive_call = archiver.export_volume_to_remote_file.call_args
        assert archive_call.args[2] == f"{tmp_dir}/pvc.tar.gz"

        # Writing the manifest + chmod-ing it, and chmod-ing + moving the temp
        # dir, are each combined into a single round trip.
        sh_calls = [c[2] for c in calls if c[0] == "sh"]
        manifest_path = f"{tmp_dir}/manifest.json"
        assert any(
            "cat >" in c and f"chmod 0600 {manifest_path}" in c for c in sh_calls
        )
        assert any(
            f"chmod 0700 {tmp_dir}" in c and f"mv {tmp_dir}" in c for c in sh_calls
        )

        # No cleanup round trip after a successful mv -- it would accomplish
        # nothing but still cost a full SSH connection.
        assert not any(c[0] == "rm" for c in calls)

    def test_cleans_up_temp_dir_on_failure(self) -> None:
        from paude.cli.backup import _write_bundle_remote

        manifest = BackupManifest(
            name="s", workspace="/w", created_at="t", source_paude_version="v"
        )
        transport = SshTransport("user@host")
        calls: list[list[str]] = []
        tmp_dir = "/tmp/.paude-backup-xyz"  # noqa: S108

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "mktemp":
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=f"{tmp_dir}\n", stderr=""
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        archiver = MagicMock()
        archiver.export_volume_to_remote_file.side_effect = RuntimeError("boom")

        with (
            patch.object(transport, "run", side_effect=fake_run),
            pytest.raises(RuntimeError, match="boom"),
        ):
            _write_bundle_remote(
                archiver,
                transport,
                "vol",
                "img",
                [],
                manifest,
                "/tmp/s.paude",  # noqa: S108
            )

        assert ["rm", "-rf", tmp_dir] in calls


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
