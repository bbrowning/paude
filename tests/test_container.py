"""Tests for container management."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest


class TestImageExists:
    """Tests for image_exists."""

    @patch("paude.container.podman.subprocess.run")
    def test_returns_true_for_existing_image(self, mock_run):
        """image_exists returns True for existing image."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "image", "exists", "test:tag"],
            returncode=0,
            stdout="",
            stderr="",
        )
        from paude.container.podman import image_exists

        result = image_exists("test:tag")
        assert result is True

    @patch("paude.container.podman.subprocess.run")
    def test_returns_false_for_missing_image(self, mock_run):
        """image_exists returns False for missing image."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "image", "exists", "test:tag"],
            returncode=1,
            stdout="",
            stderr="",
        )
        from paude.container.podman import image_exists

        result = image_exists("test:tag")
        assert result is False


class TestImageManager:
    """Tests for ImageManager."""

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_build_image_calls_podman_build(self, mock_exists, mock_run, tmp_path):
        """build_image calls podman build with correct args."""
        mock_exists.return_value = False
        from paude.container.image import ImageManager

        manager = ImageManager(script_dir=tmp_path)
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM alpine")

        manager.build_image(dockerfile, "test:tag", tmp_path)

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0]
        assert "build" in call_args
        assert "-t" in call_args
        assert "test:tag" in call_args

class TestContainerRunner:
    """Tests for ContainerRunner."""

    @patch("paude.container.runner.subprocess.run")
    def test_run_claude_includes_all_mounts(self, mock_run):
        """run_claude includes all mounts."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        mounts = ["-v", "/host:/container:rw"]
        runner.run_claude("test:image", mounts, {}, [])

        call_args = mock_run.call_args[0][0]
        assert "-v" in call_args
        assert "/host:/container:rw" in call_args

    @patch("paude.container.runner.subprocess.run")
    def test_run_claude_includes_all_env_vars(self, mock_run):
        """run_claude includes all env vars."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        env = {"TEST_VAR": "value"}
        runner.run_claude("test:image", [], env, [])

        call_args = mock_run.call_args[0][0]
        assert "-e" in call_args
        assert "TEST_VAR=value" in call_args

    @patch("paude.container.runner.subprocess.run")
    def test_run_claude_sets_working_directory(self, mock_run):
        """run_claude sets working directory with -w flag."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.run_claude("test:image", [], {}, [], workdir="/home/user/project")

        call_args = mock_run.call_args[0][0]
        assert "-w" in call_args
        w_idx = call_args.index("-w")
        assert call_args[w_idx + 1] == "/home/user/project"

    @patch("paude.container.runner.subprocess.run")
    def test_run_proxy_creates_container_with_network(self, mock_run):
        """run_proxy creates container with correct network including podman."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.run_proxy("test:proxy", "test-network")

        call_args = mock_run.call_args[0][0]
        assert "--network" in call_args
        # Should connect to both internal network and podman network
        network_idx = call_args.index("--network")
        assert call_args[network_idx + 1] == "test-network,podman"

    @patch("paude.container.runner.subprocess.run")
    def test_run_proxy_passes_dns_as_squid_env_var(self, mock_run):
        """run_proxy passes DNS as SQUID_DNS env var, not --dns flag."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.run_proxy("test:proxy", "test-network", dns="192.168.127.1")

        call_args = mock_run.call_args[0][0]
        # Should NOT use --dns flag (which requires IP, not hostname)
        assert "--dns" not in call_args
        # Should use -e SQUID_DNS=... for the squid proxy
        assert "-e" in call_args
        env_idx = call_args.index("-e")
        assert call_args[env_idx + 1] == "SQUID_DNS=192.168.127.1"

    @patch("paude.container.runner.subprocess.run")
    def test_yolo_mode_adds_skip_permissions(self, mock_run):
        """YOLO mode adds --dangerously-skip-permissions."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.run_claude("test:image", [], {}, [], yolo=True)

        call_args = mock_run.call_args[0][0]
        assert "--dangerously-skip-permissions" in call_args

    @patch("paude.container.runner.subprocess.run")
    def test_run_proxy_uses_unique_container_name(self, mock_run):
        """run_proxy uses unique container name to avoid conflicts."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        name1 = runner.run_proxy("test:proxy", "net1")
        name2 = runner.run_proxy("test:proxy", "net2")

        # Names should be unique (not both "paude-proxy")
        assert name1 != name2

    @patch("paude.container.runner.subprocess.run")
    def test_run_proxy_failure_includes_error_message(self, mock_run):
        """run_proxy raises error with stderr on failure."""
        from paude.container.runner import ContainerRunner, ProxyStartError

        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "run"],
            returncode=125,
            stdout=b"",
            stderr=b"Error: container name already in use",
        )

        runner = ContainerRunner()
        with pytest.raises(ProxyStartError, match="container name already in use"):
            runner.run_proxy("test:proxy", "test-network")

    @patch("paude.container.runner.subprocess.run")
    def test_stop_container_uses_kill_for_immediate_exit(self, mock_run):
        """stop_container uses podman kill (not stop) for immediate exit."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.stop_container("test-container")

        call_args = mock_run.call_args[0][0]
        # Should use 'kill' not 'stop' to avoid 10-second timeout
        assert call_args[0] == "podman"
        assert call_args[1] == "kill"
        assert "stop" not in call_args
        assert call_args[2] == "test-container"


class TestNetworkManager:
    """Tests for NetworkManager."""

    @patch("paude.container.network.network_exists")
    @patch("paude.container.network.run_podman")
    def test_create_internal_network_only_if_not_exists(
        self, mock_run, mock_exists
    ):
        """create_internal_network only creates if network doesn't exist."""
        mock_exists.return_value = True
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        manager.create_internal_network("paude-internal")

        # Should not call run_podman since network already exists
        mock_run.assert_not_called()

    @patch("paude.container.network.network_exists")
    @patch("paude.container.network.run_podman")
    def test_create_internal_network_creates_when_missing(
        self, mock_run, mock_exists
    ):
        """create_internal_network creates network when it doesn't exist."""
        mock_exists.return_value = False
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        manager.create_internal_network("paude-internal")

        # Should create network with --internal flag
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0]
        assert "network" in call_args
        assert "create" in call_args
        assert "--internal" in call_args
        assert "paude-internal" in call_args
