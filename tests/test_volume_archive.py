"""Tests for VolumeArchiver.export_volume (streaming, hashing, cleanup)."""

from __future__ import annotations

import hashlib
import io
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from paude.backends.podman.volume_archive import VolumeArchiver
from paude.container.engine import ContainerEngine
from paude.transport.ssh import SshTransport


@contextmanager
def _fake_stream(data: bytes = b"") -> Iterator[io.BytesIO]:
    """Stand in for ``engine.stream_run``: yield the archive bytes."""
    yield io.BytesIO(data)


@contextmanager
def _failing_stream(data: bytes = b"partial") -> Iterator[io.BytesIO]:
    """A stream_run whose command fails after producing partial output."""
    yield io.BytesIO(data)
    raise RuntimeError("archive command failed")


def _helper_name_from(call_args) -> str:
    """Extract the helper container name from a ``run ... --name NAME ...`` call."""
    args = list(call_args.args)
    return args[args.index("--name") + 1]


class TestExportVolume:
    def test_streams_bytes_hashes_and_writes(self, tmp_path) -> None:
        engine = ContainerEngine()
        payload = b"tar-archive-bytes-" * 5000
        out = tmp_path / "out.tar.gz"
        with (
            patch.object(
                engine, "stream_run", return_value=_fake_stream(payload)
            ) as mock_stream,
            patch.object(engine, "run") as mock_run,  # helper cleanup
        ):
            digest = VolumeArchiver(engine).export_volume(
                "paude-s-workspace",
                "runtime:img",
                str(out),
                exclude=[".gemini/oauth_creds.json", ".config/cursor/auth.json"],
            )

        # The stream is written verbatim and hashed on the client.
        assert out.read_bytes() == payload
        assert digest == hashlib.sha256(payload).hexdigest()

        args = list(mock_stream.call_args.args)
        assert args[0] == "run"
        assert args[args.index("--entrypoint") + 1] == "sh"
        # Full read access so tar can read every file: root (DAC) + SELinux off.
        assert args[args.index("--user") + 1] == "root"
        assert args[args.index("--security-opt") + 1] == "label=disable"
        assert "paude-s-workspace:/pvc:ro" in args
        assert "runtime:img" in args

        # tar writes to stdout (-), not a file; no sha256sum runs in-container.
        script = args[args.index("-c") + 1]
        assert "tar -czf -" in script
        assert "-C /pvc ." in script
        assert "sha256sum" not in script
        assert script.count("--exclude") == 2
        assert ".gemini/oauth_creds.json" in script
        assert ".config/cursor/auth.json" in script

        # The helper is cleaned up afterward.
        helper = _helper_name_from(mock_stream.call_args)
        rm_call = mock_run.call_args_list[-1]
        assert list(rm_call.args) == ["rm", "-f", helper]
        assert rm_call.kwargs.get("check") is False

    def test_no_exclude_script_has_no_exclude(self, tmp_path) -> None:
        engine = ContainerEngine()
        with (
            patch.object(
                engine, "stream_run", return_value=_fake_stream(b"data")
            ) as mock_stream,
            patch.object(engine, "run"),
        ):
            VolumeArchiver(engine).export_volume("vol", "img", str(tmp_path / "o.tgz"))

        script = list(mock_stream.call_args.args)[-1]
        assert "--exclude" not in script

    def test_progress_receives_cumulative_counts(self, tmp_path) -> None:
        engine = ContainerEngine()
        # Just over two 1 MiB chunks, so the callback fires multiple times.
        payload = b"x" * (2 * 1024 * 1024 + 7)
        seen: list[int] = []
        with (
            patch.object(engine, "stream_run", return_value=_fake_stream(payload)),
            patch.object(engine, "run"),
        ):
            VolumeArchiver(engine).export_volume(
                "vol", "img", str(tmp_path / "o.tgz"), progress=seen.append
            )

        assert len(seen) >= 2
        assert seen == sorted(seen)  # monotonically increasing
        assert seen[-1] == len(payload)

    def test_helper_removed_even_on_failure(self, tmp_path) -> None:
        engine = ContainerEngine()
        with (
            patch.object(
                engine, "stream_run", return_value=_failing_stream()
            ) as mock_stream,
            patch.object(engine, "run") as mock_run,
        ):
            with pytest.raises(RuntimeError):
                VolumeArchiver(engine).export_volume("vol", "img", str(tmp_path / "o"))

        helper = _helper_name_from(mock_stream.call_args)
        rm_call = mock_run.call_args_list[-1]
        assert list(rm_call.args) == ["rm", "-f", helper]
        assert rm_call.kwargs.get("check") is False


class TestVolumeSizeBytes:
    def test_parses_du_output(self) -> None:
        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="123456789\t/pvc\n", stderr=""
            )
            size = VolumeArchiver(engine).volume_size_bytes("vol", "img")
        assert size == 123456789
        # Full read access so du traverses every dir (accurate size): root + SELinux off.
        du_args = list(mock_run.call_args_list[0].args)
        assert du_args[du_args.index("--user") + 1] == "root"
        assert du_args[du_args.index("--security-opt") + 1] == "label=disable"
        # Cleanup still runs.
        assert list(mock_run.call_args_list[-1].args) == [
            "rm",
            "-f",
            _helper_name_from(mock_run.call_args_list[0]),
        ]

    def test_returns_none_on_failure(self) -> None:
        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="du: cannot access"
            )
            assert VolumeArchiver(engine).volume_size_bytes("vol", "img") is None


def _ssh_engine() -> tuple[ContainerEngine, SshTransport]:
    """A ContainerEngine wired to a (mockable) SshTransport."""
    transport = SshTransport("user@host")
    return ContainerEngine(transport=transport), transport


class TestExportVolumeToRemoteFile:
    def test_reuses_stdout_tar_script_and_redirects_on_remote_host(self) -> None:
        engine, transport = _ssh_engine()
        with (
            patch.object(transport, "run_with_remote_redirect") as mock_redirect,
            patch.object(transport, "run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="deadbeef  /remote/tmp/pvc.tar.gz\n",
                stderr="",
            )
            digest = VolumeArchiver(engine).export_volume_to_remote_file(
                "paude-s-workspace",
                "runtime:img",
                "/remote/tmp/pvc.tar.gz",
                exclude=[".gemini/oauth_creds.json"],
            )

        assert digest == "deadbeef"

        cmd, remote_path = mock_redirect.call_args.args
        assert remote_path == "/remote/tmp/pvc.tar.gz"
        assert cmd[0] == "podman"
        assert cmd[1] == "run"
        assert cmd[cmd.index("--entrypoint") + 1] == "sh"
        assert cmd[cmd.index("--user") + 1] == "root"
        assert cmd[cmd.index("--security-opt") + 1] == "label=disable"
        assert "paude-s-workspace:/pvc:ro" in cmd
        assert "runtime:img" in cmd

        # Same stdout-tar script export_volume uses -- no bind mount, no
        # in-container chmod/sha256sum.
        script = cmd[cmd.index("-c") + 1]
        assert "tar -czf -" in script
        assert "-C /pvc ." in script
        assert ".gemini/oauth_creds.json" in script
        assert "sha256sum" not in script
        assert "chmod" not in script

        # chmod + sha256sum happen afterward, combined into one round trip.
        calls = [c.args[0] for c in mock_run.call_args_list]
        combined = next(c for c in calls if c[0] == "sh" and "chmod" in c[2])
        assert "chmod 0600 /remote/tmp/pvc.tar.gz" in combined[2]
        assert "sha256sum /remote/tmp/pvc.tar.gz" in combined[2]

        # The helper container is still cleaned up via engine.run -> transport.run.
        helper = cmd[cmd.index("--name") + 1]
        assert ["podman", "rm", "-f", helper] in calls

    def test_requires_ssh_backed_engine(self) -> None:
        engine = ContainerEngine()  # defaults to LocalTransport
        with pytest.raises(RuntimeError, match="SSH-backed"):
            VolumeArchiver(engine).export_volume_to_remote_file(
                "vol", "img", "/remote/pvc.tar.gz"
            )

    def test_error_propagates_and_helper_is_still_removed(self) -> None:
        engine, transport = _ssh_engine()
        with (
            patch.object(
                transport,
                "run_with_remote_redirect",
                side_effect=RuntimeError("tar failed"),
            ) as mock_redirect,
            patch.object(transport, "run") as mock_run,
        ):
            with pytest.raises(RuntimeError, match="tar failed"):
                VolumeArchiver(engine).export_volume_to_remote_file(
                    "vol", "img", "/remote/pvc.tar.gz"
                )

        cmd = mock_redirect.call_args.args[0]
        helper = cmd[cmd.index("--name") + 1]
        calls = [c.args[0] for c in mock_run.call_args_list]
        assert ["podman", "rm", "-f", helper] in calls
        # The archive step failed, so no chmod/sha256sum follow-up ran.
        assert not any(c[0] == "chmod" for c in calls)
        assert not any(c[0] == "sha256sum" for c in calls)

    def test_progress_receives_polled_remote_sizes(self) -> None:
        engine, transport = _ssh_engine()
        sizes = iter([0, 500, 1500, 1500, 1500])

        def fake_run(cmd, **kwargs):
            if cmd[0] == "sh":  # the `wc -c` poll
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=str(next(sizes, 1500)), stderr=""
                )
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="deadbeef  /r/pvc.tar.gz\n", stderr=""
            )

        def slow_redirect(cmd, remote_path, **kwargs):
            time.sleep(0.05)

        seen: list[int] = []
        with (
            patch.object(
                transport, "run_with_remote_redirect", side_effect=slow_redirect
            ),
            patch.object(transport, "run", side_effect=fake_run),
            patch("paude.backends.podman.volume_archive._REMOTE_POLL_INTERVAL", 0.01),
        ):
            VolumeArchiver(engine).export_volume_to_remote_file(
                "vol", "img", "/r/pvc.tar.gz", progress=seen.append
            )

        assert seen  # at least one poll happened before the archive finished
        assert all(isinstance(x, int) for x in seen)
