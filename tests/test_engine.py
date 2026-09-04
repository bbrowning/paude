"""Tests for ContainerEngine abstraction."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from paude.container.engine import ContainerEngine, UnsupportedEngineError
from paude.transport import LocalTransport, SshTransport
from tests.fakes import FakePopen, FakeTransport, recorded_commands


class TestContainerEngineInit:
    """Tests for ContainerEngine initialization."""

    def test_default_engine_is_podman(self) -> None:
        engine = ContainerEngine()
        assert engine.binary == "podman"

    def test_docker_engine(self) -> None:
        engine = ContainerEngine("docker")
        assert engine.binary == "docker"


class TestContainerEngineProperties:
    """Tests for engine-specific properties."""

    def test_podman_supports_secrets(self) -> None:
        assert ContainerEngine("podman").supports_secrets is True

    def test_docker_does_not_support_secrets(self) -> None:
        assert ContainerEngine("docker").supports_secrets is False

    def test_podman_supports_multi_network_create(self) -> None:
        assert ContainerEngine("podman").supports_multi_network_create is True

    def test_docker_does_not_support_multi_network_create(self) -> None:
        assert ContainerEngine("docker").supports_multi_network_create is False

    def test_podman_default_bridge_network(self) -> None:
        assert ContainerEngine("podman").default_bridge_network == "podman"

    def test_docker_default_bridge_network(self) -> None:
        assert ContainerEngine("docker").default_bridge_network == "bridge"


class TestPodmanVersionSupport:
    """Tests for the Podman networking capability guard."""

    @staticmethod
    def _engine(
        stdout: str, *, returncode: int = 0, stderr: str = ""
    ) -> ContainerEngine:
        result = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )
        transport = FakeTransport(results={"version --format json": result})
        return ContainerEngine("podman", transport=transport)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("4.0.0", (4, 0, 0)),
            ("4.9.3-rc1", (4, 9, 3)),
            ("v5.8.4-dev", (5, 8, 4)),
        ],
    )
    def test_version_parses_release_strings(
        self, raw: str, expected: tuple[int, ...]
    ) -> None:
        engine = self._engine(json.dumps({"Client": {"Version": raw}}))
        assert engine.version == expected

    def test_version_prefers_server_and_is_cached(self) -> None:
        engine = self._engine(
            json.dumps(
                {
                    "Client": {"Version": "5.8.4"},
                    "Server": {"Version": "4.9.3"},
                }
            )
        )
        assert engine.version == (4, 9, 3)
        assert engine.version == (4, 9, 3)
        assert recorded_commands(engine) == [["podman", "version", "--format", "json"]]

    @pytest.mark.parametrize(
        "raw",
        ["not-json", "{}", '{"Client":{}}', '{"Client":{"Version":"4broken"}}'],
    )
    def test_version_is_none_for_invalid_output(self, raw: str) -> None:
        assert self._engine(raw).version is None

    def test_version_is_none_for_failed_command(self) -> None:
        engine = self._engine("", returncode=125, stderr="podman unavailable")
        assert engine.version is None

    @pytest.mark.parametrize("raw", ["4.0.0", "4.9.3", "5.8.4"])
    def test_guard_accepts_supported_podman(self, raw: str) -> None:
        engine = self._engine(json.dumps({"Client": {"Version": raw}}))
        engine.ensure_supported_networking()

    def test_guard_rejects_podman_3_with_actionable_message(self) -> None:
        engine = self._engine(json.dumps({"Client": {"Version": "3.4.4"}}))
        with pytest.raises(UnsupportedEngineError, match=r"3\.4\.4.*4\.0"):
            engine.ensure_supported_networking()

    def test_guard_fails_closed_with_command_diagnostic(self) -> None:
        engine = self._engine("", returncode=125, stderr="connection refused")
        with pytest.raises(
            UnsupportedEngineError,
            match=r"podman version --format json.*exit 125: connection refused",
        ):
            engine.ensure_supported_networking()

    def test_guard_wraps_transport_failure(self) -> None:
        transport = MagicMock()
        transport.run.side_effect = OSError("podman not found")
        engine = ContainerEngine("podman", transport=transport)
        with pytest.raises(
            UnsupportedEngineError,
            match=r"podman version --format json.*command failed: podman not found",
        ):
            engine.ensure_supported_networking()

    def test_guard_never_probes_docker(self) -> None:
        engine = ContainerEngine("docker", transport=FakeTransport())
        engine.ensure_supported_networking()
        assert recorded_commands(engine) == []


class TestContainerEngineRun:
    """Tests for ContainerEngine.run method."""

    @patch("paude.transport.local.subprocess.run")
    def test_run_prepends_binary(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        engine = ContainerEngine("podman")
        engine.run("ps", "-a")
        mock_run.assert_called_once_with(
            ["podman", "ps", "-a"],
            check=True,
            capture_output=True,
            text=True,
            input=None,
            timeout=None,
        )

    def test_stream_run_prepends_binary_and_yields_stdout(self) -> None:
        engine = ContainerEngine("podman")
        fake = FakePopen(stdout=b"payload-bytes")
        with patch.object(
            engine._transport, "popen_binary", return_value=fake
        ) as mock_pb:
            with engine.stream_run("run", "--rm", "img") as stream:
                data = stream.read()
        assert data == b"payload-bytes"
        mock_pb.assert_called_once_with(["podman", "run", "--rm", "img"])

    def test_stream_run_raises_with_stderr_on_nonzero_exit(self) -> None:
        engine = ContainerEngine("podman")
        fake = FakePopen(stderr=b"tar: fatal error\n", returncode=2)
        with patch.object(engine._transport, "popen_binary", return_value=fake):
            with pytest.raises(RuntimeError, match="tar: fatal error"):
                with engine.stream_run("run", "img") as stream:
                    stream.read()

    def test_stream_run_falls_back_to_exit_code_without_stderr(self) -> None:
        engine = ContainerEngine("podman")
        fake = FakePopen(returncode=2)
        with patch.object(engine._transport, "popen_binary", return_value=fake):
            with pytest.raises(RuntimeError, match="exit 2"):
                with engine.stream_run("run", "img") as stream:
                    stream.read()

    def test_stream_run_kills_process_still_running_after_normal_return(self) -> None:
        """A caller that returns without draining to EOF must not hang forever."""
        engine = ContainerEngine("podman")
        fake = FakePopen(stdout=b"partial-data", pending_waits=1)
        with patch.object(engine._transport, "popen_binary", return_value=fake):
            with engine.stream_run("run", "img") as stream:
                stream.read(3)
        assert fake.killed is True

    def test_stream_run_does_not_kill_process_that_exits_within_grace(self) -> None:
        engine = ContainerEngine("podman")
        fake = FakePopen(stdout=b"payload-bytes")
        with patch.object(engine._transport, "popen_binary", return_value=fake):
            with engine.stream_run("run", "img") as stream:
                stream.read()
        assert fake.killed is False

    @patch("paude.transport.local.subprocess.run")
    def test_run_docker_binary(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        engine = ContainerEngine("docker")
        engine.run("images")
        mock_run.assert_called_once_with(
            ["docker", "images"],
            check=True,
            capture_output=True,
            text=True,
            input=None,
            timeout=None,
        )

    @patch("paude.transport.local.subprocess.run")
    def test_run_no_check(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        engine = ContainerEngine()
        result = engine.run("bad-cmd", check=False)
        assert result.returncode == 1

    @patch("paude.transport.local.subprocess.run")
    def test_run_no_capture(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine()
        engine.run("ps", capture=False)
        mock_run.assert_called_once_with(
            ["podman", "ps"],
            check=True,
            capture_output=False,
            text=True,
            input=None,
            timeout=None,
        )


class TestContainerEnginePopenWithRemoteRedirect:
    """Tests for ContainerEngine.popen_with_remote_redirect method."""

    def test_prepends_binary_and_delegates_to_transport(self) -> None:
        transport = SshTransport("user@host")
        engine = ContainerEngine("podman", transport=transport)
        fake_proc = MagicMock()
        with patch.object(
            transport, "popen_remote_redirect", return_value=fake_proc
        ) as mock_redirect:
            result = engine.popen_with_remote_redirect(
                "run", "img", remote_output_path="/remote/out.tar.gz"
            )
        mock_redirect.assert_called_once_with(
            ["podman", "run", "img"], "/remote/out.tar.gz"
        )
        assert result is fake_proc

    def test_requires_ssh_backed_transport(self) -> None:
        engine = ContainerEngine("podman", transport=LocalTransport())
        with pytest.raises(RuntimeError, match="SSH-backed"):
            engine.popen_with_remote_redirect("run", "img", remote_output_path="/out")


class TestContainerEngineImageExists:
    """Tests for ContainerEngine.image_exists method."""

    @patch("paude.transport.local.subprocess.run")
    def test_podman_image_exists_uses_podman_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.image_exists("myimage:latest") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "image", "exists", "myimage:latest"]

    @patch("paude.transport.local.subprocess.run")
    def test_docker_image_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.image_exists("myimage:latest") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "image", "inspect", "myimage:latest"]

    @patch("paude.transport.local.subprocess.run")
    def test_image_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        engine = ContainerEngine("podman")
        assert engine.image_exists("nonexistent:latest") is False


class TestContainerEngineNetworkExists:
    """Tests for ContainerEngine.network_exists method."""

    @patch("paude.transport.local.subprocess.run")
    def test_podman_network_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.network_exists("mynet") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "network", "exists", "mynet"]

    @patch("paude.transport.local.subprocess.run")
    def test_docker_network_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.network_exists("mynet") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "network", "inspect", "mynet"]


class TestContainerEngineVolumeExists:
    """Tests for ContainerEngine.volume_exists method."""

    @patch("paude.transport.local.subprocess.run")
    def test_podman_volume_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.volume_exists("myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "volume", "exists", "myvol"]

    @patch("paude.transport.local.subprocess.run")
    def test_docker_volume_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.volume_exists("myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "volume", "inspect", "myvol"]


class TestContainerEngineContainerExists:
    """Tests for ContainerEngine.container_exists method."""

    @patch("paude.transport.local.subprocess.run")
    def test_podman_container_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.container_exists("mycontainer") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "container", "exists", "mycontainer"]

    @patch("paude.transport.local.subprocess.run")
    def test_docker_container_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.container_exists("mycontainer") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "container", "inspect", "mycontainer"]

    @patch("paude.transport.local.subprocess.run")
    def test_container_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        engine = ContainerEngine("docker")
        assert engine.container_exists("missing") is False


class TestContainerEngineExists:
    """Tests for the _exists helper method."""

    @patch("paude.transport.local.subprocess.run")
    def test_podman_exists_uses_exists_subcmd(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine._exists("volume", "myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "volume", "exists", "myvol"]

    @patch("paude.transport.local.subprocess.run")
    def test_docker_exists_uses_inspect_subcmd(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine._exists("volume", "myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "volume", "inspect", "myvol"]

    @patch("paude.transport.local.subprocess.run")
    def test_exists_returns_false_on_nonzero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        engine = ContainerEngine("podman")
        assert engine._exists("image", "missing") is False


class TestContainerEngineGpuArgs:
    """Tests for ContainerEngine.gpu_args method."""

    def test_docker_gpu_args(self) -> None:
        engine = ContainerEngine("docker")
        assert engine.gpu_args("all") == ["--gpus", "all"]

    def test_podman_gpu_args(self) -> None:
        engine = ContainerEngine("podman")
        assert engine.gpu_args("all") == ["--device", "nvidia.com/gpu=all"]


class TestContainerEngineNetworkArgs:
    """Tests for ContainerEngine.network_args method."""

    def test_podman_embeds_ip_in_network(self) -> None:
        engine = ContainerEngine("podman")
        assert engine.network_args("net", "10.0.0.5") == [
            "--network",
            "net:ip=10.0.0.5",
        ]

    def test_docker_passes_ip_as_separate_flag(self) -> None:
        engine = ContainerEngine("docker")
        assert engine.network_args("net", "10.0.0.5") == [
            "--network",
            "net",
            "--ip",
            "10.0.0.5",
        ]

    def test_network_without_ip(self) -> None:
        assert ContainerEngine("podman").network_args("net") == ["--network", "net"]
        assert ContainerEngine("docker").network_args("net") == ["--network", "net"]


class TestContainerEngineImageNameFormat:
    """Tests for ContainerEngine.image_name_format property."""

    def test_podman_image_name_format(self) -> None:
        engine = ContainerEngine("podman")
        assert engine.image_name_format == "{{.ImageName}}"

    def test_docker_image_name_format(self) -> None:
        engine = ContainerEngine("docker")
        assert engine.image_name_format == "{{.Config.Image}}"


class TestContainerEngineTransport:
    """Tests for transport integration."""

    def test_default_transport_is_local(self) -> None:
        engine = ContainerEngine()
        assert isinstance(engine._transport, LocalTransport)
        assert engine.is_remote is False
        assert engine.host_label == "local"

    def test_custom_transport(self) -> None:
        transport = SshTransport("user@gpu-server")
        engine = ContainerEngine("docker", transport=transport)
        assert engine.is_remote is True
        assert engine.host_label == "user@gpu-server"
        assert engine.transport is transport

    @patch("paude.transport.ssh.subprocess.run")
    def test_ssh_transport_prefixes_commands(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        transport = SshTransport("user@host")
        engine = ContainerEngine("docker", transport=transport)
        engine.run("ps", "-a", check=False)
        args = mock_run.call_args[0][0]
        assert args[0] == "ssh"
        assert "user@host" in args
        # Should end with: ... -- 'docker ps -a' (shell-quoted single string)
        idx = args.index("--")
        assert args[idx + 1 :] == ["docker ps -a"]

    @patch("paude.transport.ssh.subprocess.run")
    def test_run_interactive_through_transport(self, mock_run: MagicMock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=42)
        transport = SshTransport("user@host")
        engine = ContainerEngine("docker", transport=transport)
        rc = engine.run_interactive("exec", "-it", "ctr", "bash")
        assert rc == 42
        args = mock_run.call_args[0][0]
        assert "-t" in args  # SSH TTY flag
