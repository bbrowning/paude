"""Tests for VolumeArchiver.export_volume (streaming, hashing, cleanup)."""

from __future__ import annotations

import hashlib
import io
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from paude.backends.podman.volume_archive import VolumeArchiver
from paude.container.engine import ContainerEngine


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
