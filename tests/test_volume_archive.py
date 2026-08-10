"""Tests for VolumeArchiver.export_volume (arg construction + cleanup)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from paude.backends.podman.volume_archive import (
    CONTAINER_ARCHIVE_PATH,
    VolumeArchiver,
)
from paude.container.engine import ContainerEngine


def _helper_name_from(run_call_args) -> str:
    """Extract the helper container name from a `run ... --name NAME ...` call."""
    args = list(run_call_args.args)
    return args[args.index("--name") + 1]


def _ok(stdout: str = "abc123\n") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class TestExportVolume:
    def test_runs_sh_script_returns_digest_and_copies_out(self) -> None:
        engine = ContainerEngine()
        with (
            patch.object(engine, "run", return_value=_ok("deadbeef\n")) as mock_run,
            patch(
                "paude.backends.podman.volume_archive.copy_from_container"
            ) as mock_copy,
        ):
            digest = VolumeArchiver(engine).export_volume(
                "paude-s-workspace",
                "runtime:img",
                "/local/out.tar.gz",
                exclude=[".gemini/oauth_creds.json", ".config/cursor/auth.json"],
            )

        # The sha256 is captured from the container run — no second local read.
        assert digest == "deadbeef"

        run_call = mock_run.call_args_list[0]
        args = list(run_call.args)
        assert args[0] == "run"
        assert args[args.index("--entrypoint") + 1] == "sh"
        assert "paude-s-workspace:/pvc:ro" in args
        assert "runtime:img" in args

        script = args[args.index("-c") + 1]
        assert f"tar -czf {CONTAINER_ARCHIVE_PATH}" in script
        assert "-C /pvc ." in script
        assert f"sha256sum {CONTAINER_ARCHIVE_PATH}" in script
        # Every exclude glob is passed through.
        assert script.count("--exclude") == 2
        assert ".gemini/oauth_creds.json" in script
        assert ".config/cursor/auth.json" in script

        helper = _helper_name_from(run_call)
        mock_copy.assert_called_once_with(
            engine, helper, CONTAINER_ARCHIVE_PATH, "/local/out.tar.gz"
        )

    def test_no_exclude_script_has_no_exclude(self) -> None:
        engine = ContainerEngine()
        with (
            patch.object(engine, "run", return_value=_ok()) as mock_run,
            patch("paude.backends.podman.volume_archive.copy_from_container"),
        ):
            VolumeArchiver(engine).export_volume("vol", "img", "/out.tgz")

        script = list(mock_run.call_args_list[0].args)[-1]
        assert "--exclude" not in script

    def test_helper_removed_even_on_copy_failure(self) -> None:
        engine = ContainerEngine()
        with (
            patch.object(engine, "run", return_value=_ok()) as mock_run,
            patch(
                "paude.backends.podman.volume_archive.copy_from_container",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with pytest.raises(RuntimeError, match="boom"):
                VolumeArchiver(engine).export_volume("vol", "img", "/out.tgz")

        helper = _helper_name_from(mock_run.call_args_list[0])
        # Last engine.run call is the tolerant `rm -f <helper>` cleanup.
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
