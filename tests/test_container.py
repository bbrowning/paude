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

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_ensure_default_image_builds_runtime_layer(self, mock_exists, mock_run, tmp_path):
        """ensure_default_image builds a runtime layer with Claude."""
        import os

        # Create test containers directory structure
        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "Dockerfile").write_text("FROM centos:stream9")
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        # First call: base image doesn't exist, second: runtime doesn't exist
        mock_exists.side_effect = [False, False]

        with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
            from paude.container.image import ImageManager

            manager = ImageManager(script_dir=tmp_path)
            result = manager.ensure_default_image()

        # Should build base image then runtime layer
        assert mock_run.call_count == 2
        # First call builds base, second builds runtime
        first_call = mock_run.call_args_list[0][0]
        assert "paude-base-centos9" in str(first_call)
        second_call = mock_run.call_args_list[1][0]
        assert "paude-runtime:" in str(second_call)
        assert "paude-runtime:" in result

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_ensure_default_image_uses_cached_runtime(self, mock_exists, mock_run, tmp_path):
        """ensure_default_image skips build if runtime image is cached."""
        import os

        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "Dockerfile").write_text("FROM centos:stream9")
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        # Base exists, runtime exists
        mock_exists.return_value = True

        with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
            from paude.container.image import ImageManager

            manager = ImageManager(script_dir=tmp_path)
            result = manager.ensure_default_image()

        # No builds should happen
        mock_run.assert_not_called()
        assert "paude-runtime:" in result

class TestPrepareBuiltContext:
    """Tests for prepare_build_context."""

    def test_custom_dockerfile_remote_build_includes_claude(self, tmp_path):
        """Remote build with custom Dockerfile uses multi-stage and includes Claude."""
        import shutil

        from paude.config.models import PaudeConfig
        from paude.container.image import prepare_build_context

        # Create a custom Dockerfile
        dockerfile_path = tmp_path / "Dockerfile"
        dockerfile_path.write_text("FROM python:3.11-slim\nRUN echo hello\n")

        # Create entrypoints
        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        config = PaudeConfig(dockerfile=dockerfile_path)

        ctx = prepare_build_context(
            config,
            script_dir=tmp_path,
            for_remote_build=True,
        )

        try:
            dockerfile_content = ctx.dockerfile_path.read_text()
            # Should have multi-stage build with user-base
            assert "AS user-base" in dockerfile_content, "Should have stage 1 AS user-base"
            assert "FROM user-base" in dockerfile_content, "Should have stage 2 FROM user-base"
            # Should include Claude installation in stage 2
            assert "claude.ai/install.sh" in dockerfile_content, (
                "Multi-stage build should include Claude installation"
            )
            # Stage 2 should start with USER root to handle non-root base images
            stage2_start = dockerfile_content.find("FROM user-base")
            stage2_content = dockerfile_content[stage2_start:]
            first_user = stage2_content.find("USER ")
            user_line = stage2_content[first_user:stage2_content.find("\n", first_user)]
            assert "USER root" == user_line.strip(), (
                f"Stage 2 should start with USER root, got '{user_line.strip()}'"
            )
        finally:
            shutil.rmtree(ctx.context_dir)

    def test_default_image_always_includes_claude_install(self, tmp_path):
        """prepare_build_context always includes Claude installation for default image."""
        import os
        import shutil

        from paude.config.models import PaudeConfig
        from paude.container.image import prepare_build_context

        config = PaudeConfig()

        # Create minimal script_dir structure
        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        with patch("paude.container.image.image_exists", return_value=True):
            with patch("paude.container.image.run_podman"):
                with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                    ctx = prepare_build_context(
                        config,
                        script_dir=tmp_path,
                        for_remote_build=True,
                    )

        try:
            dockerfile_content = ctx.dockerfile_path.read_text()
            assert "claude.ai/install.sh" in dockerfile_content
        finally:
            shutil.rmtree(ctx.context_dir)

    def test_feature_injection_only_replaces_first_user_paude(self, tmp_path):
        """Feature injection replaces only the first USER paude occurrence."""
        import os
        import shutil

        from paude.config.models import FeatureSpec, PaudeConfig
        from paude.container.image import prepare_build_context

        # Create a config with pip_install AND features
        config = PaudeConfig(pip_install=True)

        # We need to mock the feature downloader (called from installer.py)
        with patch("paude.features.downloader.download_feature") as mock_download:
            # Create fake feature directory
            feature_dir = tmp_path / "feature_cache" / "abc123"
            feature_dir.mkdir(parents=True)
            (feature_dir / "install.sh").write_text("#!/bin/bash\necho test")
            (feature_dir / "devcontainer-feature.json").write_text('{"id": "test"}')
            mock_download.return_value = feature_dir

            # Add a feature to config
            config.features = [FeatureSpec(url="ghcr.io/test/feature:1", options={})]

            # Create minimal script_dir structure
            containers_dir = tmp_path / "containers" / "paude"
            containers_dir.mkdir(parents=True)
            (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
            (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

            with patch("paude.container.image.image_exists", return_value=True):
                with patch("paude.container.image.run_podman"):
                    with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                        ctx = prepare_build_context(
                            config,
                            script_dir=tmp_path,
                            for_remote_build=True,
                        )

        try:
            dockerfile_content = ctx.dockerfile_path.read_text()
            # Features should only be injected once
            feature_count = dockerfile_content.count("# Feature: test")
            assert feature_count == 1, f"Feature should appear once, found {feature_count} times"
        finally:
            shutil.rmtree(ctx.context_dir)

    def test_features_injected_without_pip_install(self, tmp_path):
        """Features are injected even without pip_install on default paude image.

        This tests the edge case where someone uses features but not pip_install.
        The Dockerfile must still have USER paude for feature injection to work.
        """
        import os
        import shutil

        from paude.config.models import FeatureSpec, PaudeConfig
        from paude.container.image import prepare_build_context

        # Create a config with features but NO pip_install
        config = PaudeConfig(pip_install=False)

        with patch("paude.features.downloader.download_feature") as mock_download:
            # Create fake feature directory
            feature_dir = tmp_path / "feature_cache" / "abc123"
            feature_dir.mkdir(parents=True)
            (feature_dir / "install.sh").write_text("#!/bin/bash\necho test")
            (feature_dir / "devcontainer-feature.json").write_text('{"id": "myfeature"}')
            mock_download.return_value = feature_dir

            # Add a feature to config
            config.features = [FeatureSpec(url="ghcr.io/test/myfeature:1", options={})]

            # Create minimal script_dir structure
            containers_dir = tmp_path / "containers" / "paude"
            containers_dir.mkdir(parents=True)
            (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
            (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

            with patch("paude.container.image.image_exists", return_value=True):
                with patch("paude.container.image.run_podman"):
                    with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                        ctx = prepare_build_context(
                            config,
                            script_dir=tmp_path,
                            for_remote_build=True,
                        )

        try:
            dockerfile_content = ctx.dockerfile_path.read_text()
            # Features should be injected
            assert "# Feature: myfeature" in dockerfile_content, (
                "Feature should be injected even without pip_install"
            )
        finally:
            shutil.rmtree(ctx.context_dir)


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
    def test_stop_container_uses_stop_with_short_timeout(self, mock_run):
        """stop_container uses podman stop with short timeout for graceful exit."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.stop_container("test-container")

        call_args = mock_run.call_args[0][0]
        # Should use 'stop' with 1-second timeout (squid has shutdown_lifetime=0)
        assert call_args[0] == "podman"
        assert call_args[1] == "stop"
        assert "-t" in call_args
        assert "1" in call_args
        assert "test-container" in call_args


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
