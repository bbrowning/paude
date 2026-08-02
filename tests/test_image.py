"""Tests for container image management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from paude.container.image import ImageManager, _detect_native_platform


class TestDetectNativePlatform:
    """Tests for _detect_native_platform()."""

    @patch("platform.machine", return_value="arm64")
    def test_arm64_mac(self, mock_machine: object) -> None:
        assert _detect_native_platform() == "linux/arm64"

    @patch("platform.machine", return_value="aarch64")
    def test_aarch64_linux(self, mock_machine: object) -> None:
        assert _detect_native_platform() == "linux/arm64"

    @patch("platform.machine", return_value="x86_64")
    def test_x86_64(self, mock_machine: object) -> None:
        assert _detect_native_platform() == "linux/amd64"

    @patch("platform.machine", return_value="AMD64")
    def test_case_insensitive(self, mock_machine: object) -> None:
        assert _detect_native_platform() == "linux/amd64"


class TestImageManagerPlatform:
    """Tests for ImageManager platform auto-detection."""

    @patch("paude.container.image._detect_native_platform", return_value="linux/arm64")
    def test_none_platform_auto_detects(self, mock_detect: object) -> None:
        mgr = ImageManager(platform=None)
        assert mgr.platform == "linux/arm64"

    def test_explicit_platform_used(self) -> None:
        mgr = ImageManager(platform="linux/amd64")
        assert mgr.platform == "linux/amd64"

    @patch("paude.container.image._detect_native_platform", return_value="linux/arm64")
    def test_default_platform_auto_detects(self, mock_detect: object) -> None:
        mgr = ImageManager()
        assert mgr.platform == "linux/arm64"


class TestImageManagerComposition:
    """Tests for composition-aware image identity."""

    def test_cache_fingerprint_includes_all_agents_and_providers(self) -> None:
        from paude.agents import get_agents

        composition = get_agents(
            ["gascity", "claude", "codex"],
            providers={"gascity": "vertex", "claude": "vertex", "codex": "chatgpt"},
            include_bundled=False,
        )
        manager = ImageManager(composition=composition)

        assert manager._composition_fingerprint() == (
            "gascity:vertex,claude:vertex,codex:chatgpt"
        )


class TestFreshBuild:
    """Tests for cache-bypassing image refreshes."""

    def test_local_fresh_build_pulls_and_disables_cache(self) -> None:
        engine = MagicMock()
        engine.is_remote = False
        manager = ImageManager(engine=engine, platform="linux/amd64")

        manager.build_image(Path("Dockerfile"), "test:latest", Path("."), fresh=True)

        args = engine.run.call_args.args
        assert "--pull" in args
        assert "--no-cache" in args

    def test_normal_build_keeps_cache_available(self) -> None:
        engine = MagicMock()
        engine.is_remote = False
        manager = ImageManager(engine=engine, platform="linux/amd64")

        manager.build_image(Path("Dockerfile"), "test:latest", Path("."))

        args = engine.run.call_args.args
        assert "--pull" not in args
        assert "--no-cache" not in args

    def test_fresh_local_wrapper_can_skip_registry_pull(self) -> None:
        engine = MagicMock()
        engine.is_remote = False
        manager = ImageManager(engine=engine, platform="linux/amd64")

        manager.build_image(
            Path("Dockerfile"),
            "test:latest",
            Path("."),
            fresh=True,
            pull=False,
        )

        args = engine.run.call_args.args
        assert "--pull" not in args
        assert "--no-cache" in args
