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
    def test_ensure_default_image_builds_runtime_layer(
        self, mock_exists, mock_run, tmp_path
    ):
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
    def test_ensure_default_image_uses_cached_runtime(
        self, mock_exists, mock_run, tmp_path
    ):
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

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_ensure_proxy_image_builds_when_missing(
        self, mock_exists, mock_run, tmp_path
    ):
        """ensure_proxy_image builds proxy image when it doesn't exist."""
        import os

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        mock_exists.return_value = False

        with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
            from paude.container.image import ImageManager

            manager = ImageManager(script_dir=tmp_path, platform="linux/amd64")
            result = manager.ensure_proxy_image()

        assert result == "paude-proxy-centos9:latest-amd64"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0]
        assert "build" in call_args

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_ensure_proxy_image_skips_build_when_cached(
        self, mock_exists, mock_run, tmp_path
    ):
        """ensure_proxy_image skips build when image already exists."""
        import os

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        mock_exists.return_value = True

        with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
            from paude.container.image import ImageManager

            manager = ImageManager(script_dir=tmp_path, platform="linux/amd64")
            result = manager.ensure_proxy_image()

        assert result == "paude-proxy-centos9:latest-amd64"
        mock_run.assert_not_called()

    @patch("paude.container.image.run_podman")
    @patch("paude.container.image.image_exists")
    def test_ensure_proxy_image_force_rebuild_ignores_cache(
        self, mock_exists, mock_run, tmp_path
    ):
        """ensure_proxy_image rebuilds when force_rebuild=True even if cached."""
        import os

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        mock_exists.return_value = True  # Image exists

        with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
            from paude.container.image import ImageManager

            manager = ImageManager(script_dir=tmp_path, platform="linux/amd64")
            result = manager.ensure_proxy_image(force_rebuild=True)

        assert result == "paude-proxy-centos9:latest-amd64"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0]
        assert "build" in call_args


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
            assert "AS user-base" in dockerfile_content, (
                "Should have stage 1 AS user-base"
            )
            assert "FROM user-base" in dockerfile_content, (
                "Should have stage 2 FROM user-base"
            )
            # Should include Claude installation in stage 2
            assert "claude.ai/install.sh" in dockerfile_content, (
                "Multi-stage build should include Claude installation"
            )
            # Stage 2 should start with USER root to handle non-root base images
            stage2_start = dockerfile_content.find("FROM user-base")
            stage2_content = dockerfile_content[stage2_start:]
            first_user = stage2_content.find("USER ")
            user_line = stage2_content[
                first_user : stage2_content.find("\n", first_user)
            ]
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

        # Create a config with features
        config = PaudeConfig()

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
            (containers_dir / "entrypoint-session.sh").write_text(
                "#!/bin/bash\nexec $@"
            )

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
            assert feature_count == 1, (
                f"Feature should appear once, found {feature_count} times"
            )
        finally:
            shutil.rmtree(ctx.context_dir)

    def test_features_injected_on_default_paude_image(self, tmp_path):
        """Features are injected on default paude image.

        The Dockerfile must have USER paude for feature injection to work.
        """
        import os
        import shutil

        from paude.config.models import FeatureSpec, PaudeConfig
        from paude.container.image import prepare_build_context

        # Create a config with features
        config = PaudeConfig()

        with patch("paude.features.downloader.download_feature") as mock_download:
            # Create fake feature directory
            feature_dir = tmp_path / "feature_cache" / "abc123"
            feature_dir.mkdir(parents=True)
            (feature_dir / "install.sh").write_text("#!/bin/bash\necho test")
            (feature_dir / "devcontainer-feature.json").write_text(
                '{"id": "myfeature"}'
            )
            mock_download.return_value = feature_dir

            # Add a feature to config
            config.features = [FeatureSpec(url="ghcr.io/test/myfeature:1", options={})]

            # Create minimal script_dir structure
            containers_dir = tmp_path / "containers" / "paude"
            containers_dir.mkdir(parents=True)
            (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
            (containers_dir / "entrypoint-session.sh").write_text(
                "#!/bin/bash\nexec $@"
            )

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
                "Feature should be injected"
            )
        finally:
            shutil.rmtree(ctx.context_dir)


class TestContainerRunner:
    """Tests for ContainerRunner."""

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
    def test_run_proxy_passes_allowed_domains_as_env_var(self, mock_run):
        """run_proxy passes allowed_domains as ALLOWED_DOMAINS env var."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        allowed_domains = [".googleapis.com", ".pypi.org", "api.example.com"]
        runner.run_proxy("test:proxy", "test-network", allowed_domains=allowed_domains)

        call_args = mock_run.call_args[0][0]
        # Should use -e ALLOWED_DOMAINS=...
        assert "-e" in call_args
        # Find the ALLOWED_DOMAINS env var
        env_indices = [i for i, x in enumerate(call_args) if x == "-e"]
        found_domains = False
        for idx in env_indices:
            if call_args[idx + 1].startswith("ALLOWED_DOMAINS="):
                found_domains = True
                expected = "ALLOWED_DOMAINS=.googleapis.com,.pypi.org,api.example.com"
                assert call_args[idx + 1] == expected
                break
        assert found_domains, "ALLOWED_DOMAINS env var not found in command"

    @patch("paude.container.runner.subprocess.run")
    def test_run_proxy_omits_allowed_domains_when_none(self, mock_run):
        """run_proxy omits ALLOWED_DOMAINS env var when not provided."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.run_proxy("test:proxy", "test-network")

        call_args = mock_run.call_args[0][0]
        # Should not have ALLOWED_DOMAINS
        env_indices = [i for i, x in enumerate(call_args) if x == "-e"]
        for idx in env_indices:
            assert not call_args[idx + 1].startswith("ALLOWED_DOMAINS="), (
                "ALLOWED_DOMAINS should not be set when not provided"
            )

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
    def test_create_internal_network_only_if_not_exists(self, mock_run, mock_exists):
        """create_internal_network only creates if network doesn't exist."""
        mock_exists.return_value = True
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        manager.create_internal_network("paude-internal")

        # Should not call run_podman since network already exists
        mock_run.assert_not_called()

    @patch("paude.container.network.network_exists")
    @patch("paude.container.network.run_podman")
    def test_create_internal_network_creates_when_missing(self, mock_run, mock_exists):
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

    @patch("paude.container.network.network_exists")
    @patch("paude.container.network.run_podman")
    def test_remove_network_calls_podman_when_exists(self, mock_run, mock_exists):
        """remove_network calls run_podman when network exists."""
        mock_exists.return_value = True
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        manager.remove_network("paude-internal")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0]
        assert "network" in call_args
        assert "rm" in call_args
        assert "paude-internal" in call_args

    @patch("paude.container.network.network_exists")
    @patch("paude.container.network.run_podman")
    def test_remove_network_does_nothing_when_not_exists(self, mock_run, mock_exists):
        """remove_network does nothing when network doesn't exist."""
        mock_exists.return_value = False
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        manager.remove_network("paude-internal")

        mock_run.assert_not_called()

    @patch("paude.container.network.network_exists")
    def test_network_exists_returns_true(self, mock_exists):
        """network_exists returns True when underlying function returns True."""
        mock_exists.return_value = True
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        result = manager.network_exists("paude-internal")

        assert result is True

    @patch("paude.container.network.network_exists")
    def test_network_exists_returns_false(self, mock_exists):
        """network_exists returns False when underlying function returns False."""
        mock_exists.return_value = False
        from paude.container.network import NetworkManager

        manager = NetworkManager()
        result = manager.network_exists("paude-internal")

        assert result is False


class TestProxyDockerfileCopyFiles:
    """Validate that all files referenced in proxy Dockerfile COPY exist."""

    def test_all_copy_sources_exist(self):
        """Every file referenced in a COPY instruction must exist in containers/proxy/."""
        import re
        from pathlib import Path

        proxy_dir = Path(__file__).parent.parent / "containers" / "proxy"
        dockerfile = proxy_dir / "Dockerfile"
        assert dockerfile.exists(), f"Proxy Dockerfile not found: {dockerfile}"

        content = dockerfile.read_text()
        # Match COPY lines, skipping --chmod=... or --from=... flags
        copy_pattern = re.compile(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+\S+", re.MULTILINE)

        sources = []
        for match in copy_pattern.finditer(content):
            src = match.group(1)
            # Skip multi-stage references like --from=builder
            if src.startswith("--"):
                continue
            sources.append(src)

        assert sources, "No COPY sources found in proxy Dockerfile"

        for src in sources:
            assert (proxy_dir / src).exists(), (
                f"File '{src}' referenced in proxy Dockerfile COPY "
                f"does not exist in {proxy_dir}"
            )


class TestVolumeManager:
    """Tests for VolumeManager."""

    @patch("paude.container.volume.subprocess.run")
    def test_create_volume_calls_podman(self, mock_run):
        """create_volume calls podman volume create with correct args."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "volume", "create", "test-vol"],
            returncode=0,
            stdout="test-vol\n",
            stderr="",
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.create_volume("test-vol")

        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "volume", "create", "test-vol"]
        assert result == "test-vol"

    @patch("paude.container.volume.subprocess.run")
    def test_create_volume_with_labels(self, mock_run):
        """create_volume passes labels as --label key=value."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="test-vol\n", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        manager.create_volume("test-vol", labels={"app": "paude", "env": "test"})

        call_args = mock_run.call_args[0][0]
        assert "--label" in call_args
        assert "app=paude" in call_args
        assert "env=test" in call_args

    @patch("paude.container.volume.subprocess.run")
    def test_remove_volume_calls_podman(self, mock_run):
        """remove_volume calls podman volume rm."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        manager.remove_volume("test-vol")

        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "volume", "rm", "test-vol"]

    @patch("paude.container.volume.subprocess.run")
    def test_remove_volume_with_force(self, mock_run):
        """remove_volume passes -f flag when force=True."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        manager.remove_volume("test-vol", force=True)

        call_args = mock_run.call_args[0][0]
        assert "-f" in call_args
        assert call_args == ["podman", "volume", "rm", "-f", "test-vol"]

    @patch("paude.container.volume.subprocess.run")
    def test_volume_exists_returns_true(self, mock_run):
        """volume_exists returns True when podman returns 0."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.volume_exists("test-vol")

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args == ["podman", "volume", "exists", "test-vol"]

    @patch("paude.container.volume.subprocess.run")
    def test_volume_exists_returns_false(self, mock_run):
        """volume_exists returns False when podman returns non-zero."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.volume_exists("test-vol")

        assert result is False

    @patch("paude.container.volume.subprocess.run")
    def test_get_volume_labels_returns_parsed_json(self, mock_run):
        """get_volume_labels returns parsed JSON labels."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"app": "paude", "workspace": "/test"}',
            stderr="",
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.get_volume_labels("test-vol")

        assert result == {"app": "paude", "workspace": "/test"}

    @patch("paude.container.volume.subprocess.run")
    def test_get_volume_labels_returns_empty_on_error(self, mock_run):
        """get_volume_labels returns empty dict on error."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="no such volume"
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.get_volume_labels("nonexistent-vol")

        assert result == {}

    @patch("paude.container.volume.subprocess.run")
    def test_list_volumes_returns_parsed_json(self, mock_run):
        """list_volumes returns parsed JSON list."""
        volumes_json = '[{"Name": "vol1"}, {"Name": "vol2"}]'
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=volumes_json, stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.list_volumes(label_filter="app=paude")

        assert result == [{"Name": "vol1"}, {"Name": "vol2"}]
        call_args = mock_run.call_args[0][0]
        assert "--filter" in call_args
        assert "label=app=paude" in call_args

    @patch("paude.container.volume.subprocess.run")
    def test_list_volumes_returns_empty_on_error(self, mock_run):
        """list_volumes returns empty list on error."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error"
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.list_volumes()

        assert result == []
