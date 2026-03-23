"""Tests for ContainerEngine abstraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.container.engine import ContainerEngine


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


class TestContainerEngineRun:
    """Tests for ContainerEngine.run method."""

    @patch("subprocess.run")
    def test_run_prepends_binary(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        engine = ContainerEngine("podman")
        engine.run("ps", "-a")
        mock_run.assert_called_once_with(
            ["podman", "ps", "-a"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_run_docker_binary(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        engine = ContainerEngine("docker")
        engine.run("images")
        mock_run.assert_called_once_with(
            ["docker", "images"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_run_no_check(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        engine = ContainerEngine()
        result = engine.run("bad-cmd", check=False)
        assert result.returncode == 1

    @patch("subprocess.run")
    def test_run_no_capture(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine()
        engine.run("ps", capture=False)
        mock_run.assert_called_once_with(
            ["podman", "ps"],
            check=True,
            capture_output=False,
            text=True,
        )


class TestContainerEngineImageExists:
    """Tests for ContainerEngine.image_exists method."""

    @patch("subprocess.run")
    def test_podman_image_exists_uses_podman_command(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.image_exists("myimage:latest") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "image", "exists", "myimage:latest"]

    @patch("subprocess.run")
    def test_docker_image_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.image_exists("myimage:latest") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "image", "inspect", "myimage:latest"]

    @patch("subprocess.run")
    def test_image_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        engine = ContainerEngine("podman")
        assert engine.image_exists("nonexistent:latest") is False


class TestContainerEngineNetworkExists:
    """Tests for ContainerEngine.network_exists method."""

    @patch("subprocess.run")
    def test_podman_network_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.network_exists("mynet") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "network", "exists", "mynet"]

    @patch("subprocess.run")
    def test_docker_network_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.network_exists("mynet") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "network", "inspect", "mynet"]


class TestContainerEngineVolumeExists:
    """Tests for ContainerEngine.volume_exists method."""

    @patch("subprocess.run")
    def test_podman_volume_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.volume_exists("myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "volume", "exists", "myvol"]

    @patch("subprocess.run")
    def test_docker_volume_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.volume_exists("myvol") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "volume", "inspect", "myvol"]


class TestContainerEngineContainerExists:
    """Tests for ContainerEngine.container_exists method."""

    @patch("subprocess.run")
    def test_podman_container_exists(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("podman")
        assert engine.container_exists("mycontainer") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["podman", "container", "exists", "mycontainer"]

    @patch("subprocess.run")
    def test_docker_container_exists_uses_inspect(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        assert engine.container_exists("mycontainer") is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "container", "inspect", "mycontainer"]

    @patch("subprocess.run")
    def test_container_not_found(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        engine = ContainerEngine("docker")
        assert engine.container_exists("missing") is False
