"""Tests for container image management."""

from __future__ import annotations

from unittest.mock import patch

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
