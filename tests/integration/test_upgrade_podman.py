"""Integration tests for paude upgrade on Podman backend."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.backends.base import SessionConfig
from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_YOLO,
)
from paude.backends.podman import PodmanBackend
from paude.cli.upgrade import UpgradeOverrides

from .conftest import cleanup_session

pytestmark = [pytest.mark.integration, pytest.mark.podman]

OLD_VERSION = "0.12.0"


def _exec(container: str, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run ``podman exec`` in the session container and capture output."""
    return subprocess.run(
        ["podman", "exec", container, *argv],
        capture_output=True,
        text=True,
    )


def _file_present(container: str, path: str) -> bool:
    """Return True if ``path`` exists inside the container."""
    return _exec(container, "test", "-e", path).returncode == 0


def _run_upgrade(
    name: str,
    backend: PodmanBackend,
    overrides: UpgradeOverrides,
    image: str,
    proxy_image: str,
) -> None:
    """Drive an in-place upgrade with the image build mocked (real recreate).

    Stubs only the network-heavy image rebuild so the container recreation,
    label writes, state migration, and start all run for real.
    """
    mock_im = MagicMock()
    mock_im.ensure_default_image.return_value = image
    mock_im.ensure_proxy_image.return_value = proxy_image

    with (
        patch("paude.container.ImageManager", return_value=mock_im),
        patch("paude.mounts.build_mounts", return_value=[]),
    ):
        from paude.cli.upgrade import _upgrade_podman

        _upgrade_podman(name, backend, rebuild=False, overrides=overrides)


def _create_and_start(
    backend: PodmanBackend,
    name: str,
    workspace: Path,
    image: str,
    proxy_image: str,
    *,
    agent_providers: list[tuple[str, str]],
    credential_providers: list[str],
) -> str:
    """Create + start a gemini-primary session and return the container name.

    Gemini stays primary because it is already proven to start cleanly on the
    bare test image by ``test_upgrade_preserves_volume_and_labels``. Codex, when
    present, is a secondary agent — only the primary agent's binary is installed
    at headless start, so these tests exercise host-side reconcile (config
    rewrite, auth reset, labels), not the physical binary install.
    """
    config = SessionConfig(
        name=name,
        workspace=workspace,
        image=image,
        allowed_domains=["redhat.com"],
        proxy_image=proxy_image,
        agent="gemini",
        provider="google",
        agent_providers=agent_providers,
        credential_providers=credential_providers,
    )
    backend.create_session(config)
    backend.start_session_no_attach(name)
    time.sleep(1)
    return f"paude-{name}"


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
                    "mkdir -p /pvc/workspace && "
                    "echo upgrade-test > /pvc/workspace/marker.txt && "
                    "rm -rf /home/paude/.gemini && "
                    "mkdir -p /home/paude/.gemini && "
                    "echo legacy-agent-state > /home/paude/.gemini/settings.json",
                ],
                check=True,
                capture_output=True,
            )

            # Verify old version label before upgrade
            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.version == OLD_VERSION

            # 3. Run upgrade with mocked image building
            _run_upgrade(
                unique_session_name,
                backend,
                UpgradeOverrides(),
                podman_test_image,
                podman_proxy_image,
            )

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

            # Legacy state from the old container layer was migrated to PVC.
            result = subprocess.run(
                [
                    "podman",
                    "exec",
                    container,
                    "cat",
                    "/pvc/.gemini/settings.json",
                ],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0
            assert "legacy-agent-state" in result.stdout

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

    def test_upgrade_reconciles_drifted_volume_ownership(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """A /pvc volume owned by a drifted (pre-pin) UID is reconciled to the
        pinned runtime user when the container is recreated, so owner-only agent
        credentials created before the UID pin stay readable after the upgrade.

        Regression for the reported ``paude upgrade`` failure on a codex/chatgpt
        session over a volume created before the UID/GID pin: without the
        reconcile the recreated 1000:0 container cannot read /pvc/.codex/auth.json
        (mode 600, owner ~997) and its OAuth login silently breaks.
        """
        backend = PodmanBackend()
        container = f"paude-{unique_session_name}"

        try:
            with patch("paude.__version__", OLD_VERSION):
                config = SessionConfig(
                    name=unique_session_name,
                    workspace=temp_workspace,
                    image=podman_test_image,
                    allowed_domains=["redhat.com"],
                    proxy_image=podman_proxy_image,
                    agent="gemini",
                )
                backend.create_session(config)

            backend.start_session_no_attach(unique_session_name)
            time.sleep(1)

            # Seed an owner-only credential, then simulate a pre-pin volume by
            # chowning /pvc to a UID the pinned runtime user (1000) doesn't own.
            subprocess.run(
                [
                    "podman",
                    "exec",
                    "--user",
                    "root",
                    container,
                    "sh",
                    "-c",
                    "mkdir -p /pvc/.codex && echo tok > /pvc/.codex/auth.json && "
                    "chmod 600 /pvc/.codex/auth.json && chown -R 997:997 /pvc",
                ],
                check=True,
                capture_output=True,
            )

            # The upgrade must complete (the hardened migration never aborts) and
            # the recreated container must reconcile ownership.
            _run_upgrade(
                unique_session_name,
                backend,
                UpgradeOverrides(),
                podman_test_image,
                podman_proxy_image,
            )

            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.status == "running"

            # /pvc and the owner-only auth.json are reconciled to the pinned user.
            owner = _exec(container, "stat", "-c", "%u:%g", "/pvc")
            assert owner.stdout.strip() == "1000:0", owner.stdout
            auth_owner = _exec(
                container, "stat", "-c", "%u:%g", "/pvc/.codex/auth.json"
            )
            assert auth_owner.stdout.strip() == "1000:0", auth_owner.stdout

            # The runtime user (default exec user) can now read its own token.
            auth = _exec(container, "cat", "/pvc/.codex/auth.json")
            assert auth.returncode == 0
            assert "tok" in auth.stdout

        finally:
            cleanup_session(backend, unique_session_name)


class TestPodmanUpgradeReconfigure:
    """Integration coverage for `paude upgrade` add-agent / provider-swap.

    Exercises the in-place reconfiguration paths (KNOWN_ISSUES TEST-003) against
    a real container. The per-session image build is mocked (the CI base image
    ships no agents), so assertions target the recreate/label/state/config
    behavior that runs host-side — not the physical agent-binary install, which
    needs a real rebuild. See ``_create_and_start`` for why codex is kept a
    secondary agent.
    """

    def test_upgrade_add_agent_in_place(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """`--add-agent codex` keeps the primary, unions providers, keeps state."""
        backend = PodmanBackend()

        try:
            container = _create_and_start(
                backend,
                unique_session_name,
                temp_workspace,
                podman_test_image,
                podman_proxy_image,
                agent_providers=[("gemini", "google")],
                credential_providers=["google"],
            )

            # Seed prior workspace + agent state on the volume.
            seed = _exec(
                container,
                "sh",
                "-c",
                "mkdir -p /pvc/workspace /pvc/.gemini && "
                "echo keep > /pvc/workspace/marker.txt && "
                "echo gem-state > /pvc/.gemini/settings.json",
            )
            assert seed.returncode == 0, seed.stderr

            _run_upgrade(
                unique_session_name,
                backend,
                UpgradeOverrides(add_agents=["codex"]),
                podman_test_image,
                podman_proxy_image,
            )

            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.status == "running"

            # Primary unchanged; codex added as a secondary agent.
            assert session.agent == "gemini"
            assert ("gemini", "google") in session.agent_providers
            assert ("codex", "chatgpt") in session.agent_providers
            # Credential providers unioned (add-agent path), not replaced.
            assert set(session.credential_providers) == {"google", "chatgpt"}

            # Prior state survived the recreate.
            assert "keep" in _exec(container, "cat", "/pvc/workspace/marker.txt").stdout
            assert (
                "gem-state"
                in _exec(container, "cat", "/pvc/.gemini/settings.json").stdout
            )

            # The newly-added codex triggered its config side-effect (chatgpt
            # default), written to the absolute /pvc path.
            config_toml = _exec(container, "cat", "/pvc/.codex/config.toml")
            assert config_toml.returncode == 0
            assert "paude-chatgpt-http" in config_toml.stdout
        finally:
            cleanup_session(backend, unique_session_name)

    def test_upgrade_swap_codex_chatgpt_to_openai(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Swapping codex chatgpt->openai strips config and clears auth.json."""
        backend = PodmanBackend()

        try:
            container = _create_and_start(
                backend,
                unique_session_name,
                temp_workspace,
                podman_test_image,
                podman_proxy_image,
                agent_providers=[("gemini", "google"), ("codex", "chatgpt")],
                credential_providers=["google", "chatgpt"],
            )

            # First start (chatgpt mode) writes the managed provider block.
            precheck = _exec(container, "cat", "/pvc/.codex/config.toml")
            assert precheck.returncode == 0
            assert "paude-chatgpt-http" in precheck.stdout

            # Seed a ChatGPT auth.json on the volume; the swap must clear it.
            seed = _exec(
                container,
                "sh",
                "-c",
                "mkdir -p /pvc/.codex && echo seeded > /pvc/.codex/auth.json",
            )
            assert seed.returncode == 0, seed.stderr

            _run_upgrade(
                unique_session_name,
                backend,
                UpgradeOverrides(agent_providers={"codex": "openai"}),
                podman_test_image,
                podman_proxy_image,
            )

            session = backend.get_session(unique_session_name)
            assert session is not None
            assert session.status == "running"
            assert ("codex", "openai") in session.agent_providers
            # Pure remap drops the old provider; google stays (still mapped).
            assert "chatgpt" not in session.credential_providers
            assert "openai" in session.credential_providers

            # openai reconcile strips the managed provider block.
            config_toml = _exec(container, "cat", "/pvc/.codex/config.toml")
            assert "paude-chatgpt-http" not in config_toml.stdout
            assert "model_provider" not in config_toml.stdout

            # The swap cleared the stale auth.json (regression guard for the
            # $HOME-vs-/pvc reset-path fix).
            assert not _file_present(container, "/pvc/.codex/auth.json")
        finally:
            cleanup_session(backend, unique_session_name)

    def test_upgrade_swap_codex_openai_to_chatgpt(
        self,
        require_podman: None,
        require_test_image: None,
        require_proxy_image: None,
        temp_workspace: Path,
        unique_session_name: str,
        podman_test_image: str,
        podman_proxy_image: str,
    ) -> None:
        """Reverse swap re-adds the managed block and preserves auth.json."""
        backend = PodmanBackend()

        try:
            container = _create_and_start(
                backend,
                unique_session_name,
                temp_workspace,
                podman_test_image,
                podman_proxy_image,
                agent_providers=[("gemini", "google"), ("codex", "openai")],
                credential_providers=["google", "openai"],
            )

            # Seed auth.json after first start (openai mode would have rm'd it).
            seed = _exec(
                container,
                "sh",
                "-c",
                "mkdir -p /pvc/.codex && echo seeded > /pvc/.codex/auth.json",
            )
            assert seed.returncode == 0, seed.stderr

            _run_upgrade(
                unique_session_name,
                backend,
                UpgradeOverrides(agent_providers={"codex": "chatgpt"}),
                podman_test_image,
                podman_proxy_image,
            )

            session = backend.get_session(unique_session_name)
            assert session is not None
            assert ("codex", "chatgpt") in session.agent_providers
            assert "chatgpt" in session.credential_providers

            # chatgpt reconcile re-adds the managed provider block ...
            config_toml = _exec(container, "cat", "/pvc/.codex/config.toml")
            assert config_toml.returncode == 0
            assert "paude-chatgpt-http" in config_toml.stdout
            # ... and does not delete auth.json (only openai mode clears it).
            assert _file_present(container, "/pvc/.codex/auth.json")
        finally:
            cleanup_session(backend, unique_session_name)
