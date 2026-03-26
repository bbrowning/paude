"""Tests for container management."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestImageExists:
    """Tests for image_exists."""

    @patch("paude.transport.local.subprocess.run")
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

    @patch("paude.transport.local.subprocess.run")
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

    def test_build_image_calls_podman_build(self, tmp_path):
        """build_image calls podman build with correct args."""
        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            manager = ImageManager(script_dir=tmp_path, engine=engine)
            dockerfile = tmp_path / "Dockerfile"
            dockerfile.write_text("FROM alpine")

            manager.build_image(dockerfile, "test:tag", tmp_path)

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0]
            assert "build" in call_args
            assert "-t" in call_args
            assert "test:tag" in call_args

    def test_ensure_default_image_builds_runtime_layer(self, tmp_path):
        """ensure_default_image builds a runtime layer with Claude."""
        import os

        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        # Create test containers directory structure
        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "Dockerfile").write_text("FROM centos:stream9")
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        engine = ContainerEngine()

        with (
            patch.object(engine, "image_exists") as mock_exists,
            patch.object(engine, "run") as mock_run,
        ):
            # First call: base image doesn't exist, second: runtime doesn't exist
            mock_exists.side_effect = [False, False]

            with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                manager = ImageManager(script_dir=tmp_path, engine=engine)
                result = manager.ensure_default_image()

            # Should build base image then runtime layer
            assert mock_run.call_count == 2
            # First call builds base, second builds runtime
            first_call = mock_run.call_args_list[0][0]
            assert "paude-base-centos10" in str(first_call)
            second_call = mock_run.call_args_list[1][0]
            assert "paude-runtime:" in str(second_call)
            assert "paude-runtime:" in result

    def test_ensure_default_image_uses_cached_runtime(self, tmp_path):
        """ensure_default_image skips build if runtime image is cached."""
        import os

        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "Dockerfile").write_text("FROM centos:stream9")
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        engine = ContainerEngine()

        with (
            patch.object(engine, "image_exists") as mock_exists,
            patch.object(engine, "run") as mock_run,
        ):
            # Base exists, runtime exists
            mock_exists.return_value = True

            with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                manager = ImageManager(script_dir=tmp_path, engine=engine)
                result = manager.ensure_default_image()

            # No builds should happen
            mock_run.assert_not_called()
            assert "paude-runtime:" in result

    def test_ensure_proxy_image_builds_when_missing(self, tmp_path):
        """ensure_proxy_image builds proxy image when it doesn't exist."""
        import os

        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        engine = ContainerEngine()

        with (
            patch.object(engine, "image_exists") as mock_exists,
            patch.object(engine, "run") as mock_run,
        ):
            mock_exists.return_value = False

            with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                manager = ImageManager(
                    script_dir=tmp_path, platform="linux/amd64", engine=engine
                )
                result = manager.ensure_proxy_image()

            assert result == "paude-proxy-centos10:latest-amd64"
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0]
            assert "build" in call_args

    def test_ensure_proxy_image_skips_build_when_cached(self, tmp_path):
        """ensure_proxy_image skips build when image already exists."""
        import os

        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        engine = ContainerEngine()

        with (
            patch.object(engine, "image_exists") as mock_exists,
            patch.object(engine, "run") as mock_run,
        ):
            mock_exists.return_value = True

            with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                manager = ImageManager(
                    script_dir=tmp_path, platform="linux/amd64", engine=engine
                )
                result = manager.ensure_proxy_image()

            assert result == "paude-proxy-centos10:latest-amd64"
            mock_run.assert_not_called()

    def test_ensure_proxy_image_force_rebuild_ignores_cache(self, tmp_path):
        """ensure_proxy_image rebuilds when force_rebuild=True even if cached."""
        import os

        from paude.container.engine import ContainerEngine
        from paude.container.image import ImageManager

        proxy_dir = tmp_path / "containers" / "proxy"
        proxy_dir.mkdir(parents=True)
        (proxy_dir / "Dockerfile").write_text("FROM centos:stream9")

        engine = ContainerEngine()

        with (
            patch.object(engine, "image_exists") as mock_exists,
            patch.object(engine, "run") as mock_run,
        ):
            mock_exists.return_value = True  # Image exists

            with patch.dict(os.environ, {"PAUDE_DEV": "1"}):
                manager = ImageManager(
                    script_dir=tmp_path, platform="linux/amd64", engine=engine
                )
                result = manager.ensure_proxy_image(force_rebuild=True)

            assert result == "paude-proxy-centos10:latest-amd64"
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
        from paude.container.engine import ContainerEngine
        from paude.container.image import prepare_build_context

        config = PaudeConfig()

        # Create minimal script_dir structure
        containers_dir = tmp_path / "containers" / "paude"
        containers_dir.mkdir(parents=True)
        (containers_dir / "entrypoint.sh").write_text("#!/bin/bash\nexec $@")
        (containers_dir / "entrypoint-session.sh").write_text("#!/bin/bash\nexec $@")

        engine = ContainerEngine()
        with patch.object(engine, "image_exists", return_value=True):
            with patch.object(engine, "run"):
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
        from paude.container.engine import ContainerEngine
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

            engine = ContainerEngine()
            with patch.object(engine, "image_exists", return_value=True):
                with patch.object(engine, "run"):
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
        from paude.container.engine import ContainerEngine
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

            engine = ContainerEngine()
            with patch.object(engine, "image_exists", return_value=True):
                with patch.object(engine, "run"):
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

    def test_run_proxy_creates_container_with_network(self):
        """run_proxy creates container with correct network including podman."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        proxy.run_proxy("test:proxy", "test-network")

        call_args = mock_runner.engine.run.call_args[0]
        assert "--network" in call_args
        network_idx = call_args.index("--network")
        assert call_args[network_idx + 1] == "test-network,podman"

    def test_run_proxy_passes_dns_as_squid_env_var(self):
        """run_proxy passes DNS as SQUID_DNS env var, not --dns flag."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        proxy.run_proxy("test:proxy", "test-network", dns="192.168.127.1")

        call_args = mock_runner.engine.run.call_args[0]
        assert "--dns" not in call_args
        assert "-e" in call_args
        env_idx = call_args.index("-e")
        assert call_args[env_idx + 1] == "SQUID_DNS=192.168.127.1"

    def test_run_proxy_passes_allowed_domains_as_env_var(self):
        """run_proxy passes allowed_domains as ALLOWED_DOMAINS env var."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        allowed_domains = [".googleapis.com", ".pypi.org", "api.example.com"]
        proxy.run_proxy("test:proxy", "test-network", allowed_domains=allowed_domains)

        call_args = mock_runner.engine.run.call_args[0]
        assert "-e" in call_args
        env_indices = [i for i, x in enumerate(call_args) if x == "-e"]
        found_domains = False
        for idx in env_indices:
            if call_args[idx + 1].startswith("ALLOWED_DOMAINS="):
                found_domains = True
                expected = "ALLOWED_DOMAINS=.googleapis.com,.pypi.org,api.example.com"
                assert call_args[idx + 1] == expected
                break
        assert found_domains, "ALLOWED_DOMAINS env var not found in command"

    def test_run_proxy_omits_allowed_domains_when_none(self):
        """run_proxy omits ALLOWED_DOMAINS env var when not provided."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        proxy.run_proxy("test:proxy", "test-network")

        call_args = mock_runner.engine.run.call_args[0]
        env_indices = [i for i, x in enumerate(call_args) if x == "-e"]
        for idx in env_indices:
            assert not call_args[idx + 1].startswith("ALLOWED_DOMAINS="), (
                "ALLOWED_DOMAINS should not be set when not provided"
            )

    def test_run_proxy_uses_unique_container_name(self):
        """run_proxy uses unique container name to avoid conflicts."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        name1 = proxy.run_proxy("test:proxy", "net1")
        name2 = proxy.run_proxy("test:proxy", "net2")

        assert name1 != name2

    def test_run_proxy_failure_includes_error_message(self):
        """run_proxy raises error with stderr on failure."""
        from paude.container.proxy_runner import ProxyRunner, ProxyStartError

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(
            returncode=125,
            stderr="Error: container name already in use",
        )

        proxy = ProxyRunner(mock_runner)
        with pytest.raises(ProxyStartError, match="container name already in use"):
            proxy.run_proxy("test:proxy", "test-network")

    @patch("paude.transport.local.subprocess.run")
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

    @patch("paude.transport.local.subprocess.run")
    def test_create_secret_succeeds_on_clean_install(self, mock_run):
        """create_secret works on clean install (no prior secret)."""
        mock_run.return_value = MagicMock(returncode=0)
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.create_secret("my-secret", Path("/tmp/creds.json"))

        # Only one call needed when secret doesn't already exist
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == [
            "podman",
            "secret",
            "create",
            "my-secret",
            "/tmp/creds.json",
        ]

    @patch("paude.transport.local.subprocess.run")
    def test_create_secret_replaces_existing_secret(self, mock_run):
        """create_secret removes and retries when secret already exists."""
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "podman"),  # create fails (exists)
            MagicMock(returncode=0),  # secret rm succeeds
            MagicMock(returncode=0),  # secret create succeeds on retry
        ]
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        runner.create_secret("my-secret", Path("/tmp/creds.json"))

        assert mock_run.call_count == 3
        # First call: create attempt
        assert mock_run.call_args_list[0][0][0] == [
            "podman",
            "secret",
            "create",
            "my-secret",
            "/tmp/creds.json",
        ]
        # Second call: remove existing secret
        assert mock_run.call_args_list[1][0][0] == [
            "podman",
            "secret",
            "rm",
            "my-secret",
        ]
        # Third call: retry create
        assert mock_run.call_args_list[2][0][0] == [
            "podman",
            "secret",
            "create",
            "my-secret",
            "/tmp/creds.json",
        ]

    @patch("paude.transport.local.subprocess.run")
    def test_create_container_error_includes_stderr(self, mock_run):
        """create_container raises CalledProcessError with stderr from engine."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: network not found: bad-net",
        )
        from paude.container.runner import ContainerRunner

        runner = ContainerRunner()
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            runner.create_container(
                name="test-container",
                image="test-image:latest",
                mounts=[],
                env={},
                workdir="/pvc",
                network="bad-net",
            )
        assert exc_info.value.stderr == "Error: network not found: bad-net"
        assert exc_info.value.returncode == 1


class TestNetworkManager:
    """Tests for NetworkManager."""

    def test_create_internal_network_only_if_not_exists(self):
        """create_internal_network only creates if network doesn't exist."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "network_exists", return_value=True),
            patch.object(engine, "run") as mock_run,
        ):
            manager = NetworkManager(engine)
            manager.create_internal_network("paude-internal")

            # Should not call run since network already exists
            mock_run.assert_not_called()

    def test_create_internal_network_creates_when_missing(self):
        """create_internal_network creates network when it doesn't exist."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "network_exists", return_value=False),
            patch.object(engine, "run") as mock_run,
        ):
            manager = NetworkManager(engine)
            manager.create_internal_network("paude-internal")

            # Should create network with --internal flag
            mock_run.assert_called_once()
            call_args = mock_run.call_args[0]
            assert "network" in call_args
            assert "create" in call_args
            assert "--internal" in call_args
            assert "paude-internal" in call_args

    def test_remove_network_calls_engine_when_exists(self):
        """remove_network calls engine when network exists."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "network_exists", return_value=True),
            patch.object(engine, "run") as mock_run,
        ):
            manager = NetworkManager(engine)
            manager.remove_network("paude-internal")

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0]
            assert "network" in call_args
            assert "rm" in call_args
            assert "paude-internal" in call_args

    def test_remove_network_does_nothing_when_not_exists(self):
        """remove_network does nothing when network doesn't exist."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "network_exists", return_value=False),
            patch.object(engine, "run") as mock_run,
        ):
            manager = NetworkManager(engine)
            manager.remove_network("paude-internal")

            mock_run.assert_not_called()

    def test_network_exists_returns_true(self):
        """network_exists returns True when underlying engine returns True."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with patch.object(engine, "network_exists", return_value=True):
            manager = NetworkManager(engine)
            result = manager.network_exists("paude-internal")

            assert result is True

    def test_network_exists_returns_false(self):
        """network_exists returns False when underlying engine returns False."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with patch.object(engine, "network_exists", return_value=False):
            manager = NetworkManager(engine)
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

    def test_create_volume_calls_engine(self):
        """create_volume calls engine with correct args."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test-vol\n")
            manager = VolumeManager(engine)
            result = manager.create_volume("test-vol")

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0]
            assert "volume" in call_args
            assert "create" in call_args
            assert "test-vol" in call_args
            assert result == "test-vol"

    def test_create_volume_with_labels(self):
        """create_volume passes labels as --label key=value."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="test-vol\n")
            manager = VolumeManager(engine)
            manager.create_volume("test-vol", labels={"app": "paude", "env": "test"})

            call_args = mock_run.call_args[0]
            assert "--label" in call_args
            assert "app=paude" in call_args
            assert "env=test" in call_args

    def test_remove_volume_calls_engine(self):
        """remove_volume calls engine volume rm."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager = VolumeManager(engine)
            manager.remove_volume("test-vol")

            call_args = mock_run.call_args[0]
            assert "volume" in call_args
            assert "rm" in call_args
            assert "test-vol" in call_args

    def test_remove_volume_with_force(self):
        """remove_volume passes -f flag when force=True."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager = VolumeManager(engine)
            manager.remove_volume("test-vol", force=True)

            call_args = mock_run.call_args[0]
            assert "-f" in call_args
            assert "volume" in call_args
            assert "rm" in call_args

    @patch("paude.transport.local.subprocess.run")
    def test_volume_exists_returns_true(self, mock_run):
        """volume_exists returns True when podman returns 0."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.volume_exists("test-vol")

        assert result is True

    @patch("paude.transport.local.subprocess.run")
    def test_volume_exists_returns_false(self, mock_run):
        """volume_exists returns False when podman returns non-zero."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        from paude.container.volume import VolumeManager

        manager = VolumeManager()
        result = manager.volume_exists("test-vol")

        assert result is False

    def test_get_volume_labels_returns_parsed_json(self):
        """get_volume_labels returns parsed JSON labels."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"app": "paude", "workspace": "/test"}',
            )
            manager = VolumeManager(engine)
            result = manager.get_volume_labels("test-vol")

        assert result == {"app": "paude", "workspace": "/test"}

    def test_get_volume_labels_returns_empty_on_error(self):
        """get_volume_labels returns empty dict on error."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="no such volume"
            )
            manager = VolumeManager(engine)
            result = manager.get_volume_labels("nonexistent-vol")

        assert result == {}

    def test_list_volumes_returns_parsed_json(self):
        """list_volumes returns parsed JSON list."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            volumes_json = '[{"Name": "vol1"}, {"Name": "vol2"}]'
            mock_run.return_value = MagicMock(returncode=0, stdout=volumes_json)
            manager = VolumeManager(engine)
            result = manager.list_volumes(label_filter="app=paude")

        assert result == [{"Name": "vol1"}, {"Name": "vol2"}]
        call_args = mock_run.call_args[0]
        assert "--filter" in call_args
        assert "label=app=paude" in call_args

    def test_list_volumes_returns_empty_on_error(self):
        """list_volumes returns empty list on error."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            manager = VolumeManager(engine)
            result = manager.list_volumes()

        assert result == []

    def test_remove_volume_verified_succeeds(self):
        """remove_volume_verified succeeds when volume is removed."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "run") as mock_run,
            patch.object(engine, "volume_exists", return_value=False),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            manager = VolumeManager(engine)
            manager.remove_volume_verified("test-vol")  # Should not raise

    def test_remove_volume_verified_raises_on_failure(self):
        """remove_volume_verified raises RuntimeError when volume persists."""
        from paude.container.engine import ContainerEngine
        from paude.container.volume import VolumeManager

        engine = ContainerEngine()
        with (
            patch.object(engine, "run") as mock_run,
            patch.object(engine, "volume_exists", return_value=True),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            manager = VolumeManager(engine)
            with pytest.raises(RuntimeError, match="Failed to remove volume"):
                manager.remove_volume_verified("stuck-vol")


class TestContainerRunnerVerifiedRemoval:
    """Tests for ContainerRunner.remove_container_verified."""

    def test_remove_container_verified_succeeds(self):
        """remove_container_verified succeeds when container is removed."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        engine = ContainerEngine()
        with (
            patch.object(engine, "run") as mock_run,
            patch.object(engine, "container_exists", return_value=False),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            runner = ContainerRunner(engine)
            runner.remove_container_verified("test-ctr")  # Should not raise

    def test_remove_container_verified_raises_on_failure(self):
        """remove_container_verified raises RuntimeError when container persists."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        engine = ContainerEngine()
        with (
            patch.object(engine, "run") as mock_run,
            patch.object(engine, "container_exists", return_value=True),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            runner = ContainerRunner(engine)
            with pytest.raises(RuntimeError, match="Failed to remove container"):
                runner.remove_container_verified("stuck-ctr")


class TestContainerRunnerGpu:
    """Tests for GPU passthrough in ContainerRunner.create_container."""

    @patch("paude.transport.local.subprocess.run")
    def test_docker_gpu_all(self, mock_run):
        """Docker uses --gpus all for gpu='all'."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            gpu="all",
        )
        call_args = mock_run.call_args[0][0]
        assert "--gpus" in call_args
        gpus_idx = call_args.index("--gpus")
        assert call_args[gpus_idx + 1] == "all"

    @patch("paude.transport.local.subprocess.run")
    def test_docker_gpu_specific_devices(self, mock_run):
        """Docker uses --gpus 'device=0,1' for specific devices."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            gpu="device=0,1",
        )
        call_args = mock_run.call_args[0][0]
        assert "--gpus" in call_args
        gpus_idx = call_args.index("--gpus")
        assert call_args[gpus_idx + 1] == "device=0,1"

    @patch("paude.transport.local.subprocess.run")
    def test_podman_gpu_all(self, mock_run):
        """Podman uses --device nvidia.com/gpu=all for gpu='all'."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            gpu="all",
        )
        call_args = mock_run.call_args[0][0]
        assert "--device" in call_args
        device_idx = call_args.index("--device")
        assert call_args[device_idx + 1] == "nvidia.com/gpu=all"

    @patch("paude.transport.local.subprocess.run")
    def test_podman_gpu_specific_devices(self, mock_run):
        """Podman uses CDI syntax for specific devices."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            gpu="device=0,1",
        )
        call_args = mock_run.call_args[0][0]
        assert "--device" in call_args
        device_idx = call_args.index("--device")
        assert call_args[device_idx + 1] == "nvidia.com/gpu=device=0,1"

    @patch("paude.transport.local.subprocess.run")
    def test_no_gpu_flags_when_none(self, mock_run):
        """No GPU flags added when gpu is None."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
        )
        call_args = mock_run.call_args[0][0]
        assert "--gpus" not in call_args
        assert "--device" not in call_args


class TestContainerRunnerDns:
    """Tests for DNS parameter in ContainerRunner."""

    @patch("paude.transport.local.subprocess.run")
    def test_create_container_with_dns(self, mock_run):
        """create_container passes --dns flags when dns is provided."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            dns=["10.89.0.2"],
        )
        call_args = mock_run.call_args[0][0]
        assert "--dns" in call_args
        dns_idx = call_args.index("--dns")
        assert call_args[dns_idx + 1] == "10.89.0.2"

    @patch("paude.transport.local.subprocess.run")
    def test_create_container_without_dns(self, mock_run):
        """create_container does not pass --dns when dns is None."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
        )
        call_args = mock_run.call_args[0][0]
        assert "--dns" not in call_args

    @patch("paude.transport.local.subprocess.run")
    def test_create_container_with_multiple_dns(self, mock_run):
        """create_container passes multiple --dns flags."""
        from paude.container.engine import ContainerEngine
        from paude.container.runner import ContainerRunner

        mock_run.return_value = MagicMock(
            returncode=0, stdout="container-id", stderr=""
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="test",
            image="test:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            dns=["10.89.0.2", "8.8.8.8"],
        )
        call_args = mock_run.call_args[0][0]
        dns_indices = [i for i, x in enumerate(call_args) if x == "--dns"]
        assert len(dns_indices) == 2
        assert call_args[dns_indices[0] + 1] == "10.89.0.2"
        assert call_args[dns_indices[1] + 1] == "8.8.8.8"


class TestNetworkManagerGateway:
    """Tests for NetworkManager gateway and proxy IP derivation."""

    def test_get_network_gateway_returns_ip(self):
        """get_network_gateway returns the gateway IP."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="10.89.0.1\n")
            manager = NetworkManager(engine)
            result = manager.get_network_gateway("paude-net-test")

        assert result == "10.89.0.1"
        call_args = mock_run.call_args[0]
        assert "network" in call_args
        assert "inspect" in call_args
        assert "paude-net-test" in call_args

    def test_get_network_gateway_returns_none_on_error(self):
        """get_network_gateway returns None when inspect fails."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="no such network"
            )
            manager = NetworkManager(engine)
            result = manager.get_network_gateway("nonexistent")

        assert result is None

    def test_get_network_gateway_returns_none_on_empty(self):
        """get_network_gateway returns None when output is empty."""
        from paude.container.engine import ContainerEngine
        from paude.container.network import NetworkManager

        engine = ContainerEngine()
        with patch.object(engine, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="\n")
            manager = NetworkManager(engine)
            result = manager.get_network_gateway("paude-net-test")

        assert result is None

    def test_derive_proxy_ip_increments_gateway(self):
        """derive_proxy_ip returns gateway + 1."""
        from paude.container.network import NetworkManager

        assert NetworkManager.derive_proxy_ip("10.89.0.1") == "10.89.0.2"
        assert NetworkManager.derive_proxy_ip("172.28.0.1") == "172.28.0.2"
        assert NetworkManager.derive_proxy_ip("192.168.1.1") == "192.168.1.2"


class TestProxyRunnerFixedIp:
    """Tests for ProxyRunner with fixed IP."""

    def test_create_session_proxy_passes_ip_flag(self):
        """create_session_proxy passes --ip flag when ip is provided."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        proxy.create_session_proxy(
            name="paude-proxy-test",
            image="proxy:latest",
            network="paude-net-test",
            ip="10.89.0.2",
        )

        call_args = mock_runner.engine.run.call_args[0]
        assert "--ip" in call_args
        ip_idx = call_args.index("--ip")
        assert call_args[ip_idx + 1] == "10.89.0.2"

    def test_create_session_proxy_omits_ip_when_none(self):
        """create_session_proxy does not pass --ip when ip is None."""
        from paude.container.proxy_runner import ProxyRunner

        mock_runner = MagicMock()
        mock_runner.engine.binary = "podman"
        mock_runner.engine.supports_multi_network_create = True
        mock_runner.engine.default_bridge_network = "podman"
        mock_runner.engine.run.return_value = MagicMock(returncode=0)

        proxy = ProxyRunner(mock_runner)
        proxy.create_session_proxy(
            name="paude-proxy-test",
            image="proxy:latest",
            network="paude-net-test",
        )

        call_args = mock_runner.engine.run.call_args[0]
        assert "--ip" not in call_args
