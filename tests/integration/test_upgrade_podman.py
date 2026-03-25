"""Integration tests for paude upgrade on Podman backend."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import SessionConfig
from paude.backends.podman import PodmanBackend
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_YOLO,
)

from .conftest import cleanup_session

pytestmark = [pytest.mark.integration, pytest.mark.podman]

OLD_VERSION = "0.12.0"


class TestPodmanUpgrade:
    """Test upgrade preserves volume content and session labels."""

    def test_upgrade_preserves_volume_and_labels(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Upgrade recreates container but keeps volume data and labels."""
        from paude import __version__ as current_version

        backend = PodmanBackend()
        container = f"paude-{unique_session_name}"

        try:
            # 1. Create session with simulated old version
            with patch("paude.__version__", OLD_VERSION):
                config = SessionConfig(
                    name=unique_session_name,
                    workspace=temp_workspace,
                    image=podman_test_image,
                    allowed_domains=["redhat.com"],
                    yolo=True,
                    proxy_image=podman_proxy_image,
                    agent="gemini",
                )
                backend.create_session(config)

            # 2. Start and write marker file to PVC
            backend.start_session_no_attach(unique_session_name)
            time.sleep(1)

            subprocess.run(
                [
                    "podman",
                    "exec",
                    container,
                    "sh",
                    "-c",
                    "mkdir -p /pvc/workspace && echo upgrade-test > /pvc/workspace/marker.txt",
                ],
                check=True,
                capture_output=True,
            )

            # Verify old version label before upgrade
            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.version == OLD_VERSION

            # 3. Run upgrade with mocked image building
            mock_im = MagicMock()
            mock_im.ensure_default_image.return_value = podman_test_image
            mock_im.ensure_proxy_image.return_value = podman_proxy_image

            with (
                patch("paude.cli.upgrade.ImageManager", return_value=mock_im),
                patch("paude.cli.upgrade.build_mounts", return_value=[]),
            ):
                from paude.cli.upgrade import _upgrade_podman

                _upgrade_podman(unique_session_name, backend, rebuild=False)

            # 4. Verify session is running with updated version
            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.status == "running"
            assert session.version == current_version

            # 5. Verify labels preserved on new container
            from paude.backends.podman.helpers import (
                find_container_by_session_name,
            )

            info = find_container_by_session_name(backend._runner, unique_session_name)
            assert info is not None
            labels = info.get("Labels", {})
            assert labels.get(PAUDE_LABEL_AGENT) == "gemini"
            assert labels.get(PAUDE_LABEL_YOLO) == "1"
            assert "redhat.com" in labels.get(PAUDE_LABEL_DOMAINS, "")
            assert labels.get(PAUDE_LABEL_VERSION) == current_version

            # 6. Verify marker file survived the upgrade
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container,
                    "cat",
                    "/pvc/workspace/marker.txt",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "upgrade-test" in result.stdout

            # 7. Verify proxy container exists
            result = subprocess.run(
                [
                    "podman",
                    "container",
                    "exists",
                    f"paude-proxy-{unique_session_name}",
                ],
                capture_output=True,
            )
            assert result.returncode == 0, "Proxy container should exist"

        finally:
            cleanup_session(backend, unique_session_name)
