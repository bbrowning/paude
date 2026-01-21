"""Image management for paude containers."""

from __future__ import annotations

import os
from pathlib import Path

from paude.config.models import PaudeConfig
from paude.container.podman import image_exists, run_podman
from paude.hash import compute_config_hash


class ImageManager:
    """Manages container images for paude."""

    def __init__(self, script_dir: Path | None = None):
        """Initialize the image manager.

        Args:
            script_dir: Path to the paude script directory (for dev mode).
        """
        self.script_dir = script_dir
        self.dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        self.registry = os.environ.get("PAUDE_REGISTRY", "docker.io/bbrowning")

    def ensure_default_image(self) -> str:
        """Ensure the default paude image is available.

        Returns:
            Image tag to use.
        """
        if self.dev_mode and self.script_dir:
            # Build locally in dev mode
            tag = "paude:dev"
            dockerfile = self.script_dir / "containers" / "paude" / "Dockerfile"
            context = self.script_dir / "containers" / "paude"
            self.build_image(dockerfile, tag, context)
            return tag
        else:
            # Pull from registry
            tag = f"{self.registry}/paude:latest"
            self.pull_image(tag)
            return tag

    def ensure_custom_image(
        self,
        config: PaudeConfig,
        force_rebuild: bool = False,
    ) -> str:
        """Ensure a custom workspace image is available.

        Args:
            config: Parsed paude configuration.
            force_rebuild: Force rebuild even if image exists.

        Returns:
            Image tag to use.
        """
        # Compute hash for image tag
        base_path = Path(__file__).parent.parent.parent.parent
        entrypoint = base_path / "containers" / "paude" / "entrypoint.sh"
        if self.script_dir:
            entrypoint = self.script_dir / "containers" / "paude" / "entrypoint.sh"

        config_hash = compute_config_hash(
            config.config_file,
            config.dockerfile,
            config.base_image,
            entrypoint,
        )
        tag = f"paude-workspace:{config_hash}"

        # Check if we need to build
        if not force_rebuild and image_exists(tag):
            return tag

        # Generate and build the workspace Dockerfile
        from paude.config.dockerfile import generate_workspace_dockerfile

        dockerfile_content = generate_workspace_dockerfile(config)

        # Write temporary Dockerfile
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(dockerfile_content)

            # Copy entrypoint
            entrypoint_dest = Path(tmpdir) / "entrypoint.sh"
            if entrypoint.exists():
                entrypoint_dest.write_text(entrypoint.read_text())
            else:
                # Minimal fallback
                entrypoint_dest.write_text("#!/bin/bash\nexec claude \"$@\"\n")
            entrypoint_dest.chmod(0o755)

            # Build
            build_args = {"BASE_IMAGE": config.base_image or "node:22-slim"}
            build_args.update(config.build_args)
            self.build_image(dockerfile_path, tag, Path(tmpdir), build_args)

        return tag

    def ensure_proxy_image(self) -> str:
        """Ensure the proxy image is available.

        Returns:
            Image tag to use.
        """
        if self.dev_mode and self.script_dir:
            tag = "paude-proxy:dev"
            dockerfile = self.script_dir / "containers" / "proxy" / "Dockerfile"
            context = self.script_dir / "containers" / "proxy"
            self.build_image(dockerfile, tag, context)
            return tag
        else:
            tag = f"{self.registry}/paude-proxy:latest"
            self.pull_image(tag)
            return tag

    def build_image(
        self,
        dockerfile: Path,
        tag: str,
        context: Path,
        build_args: dict[str, str] | None = None,
    ) -> None:
        """Build a container image.

        Args:
            dockerfile: Path to Dockerfile.
            tag: Image tag.
            context: Build context directory.
            build_args: Optional build arguments.
        """
        cmd = ["build", "-f", str(dockerfile), "-t", tag]
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        cmd.append(str(context))
        run_podman(*cmd, capture=False)

    def pull_image(self, image: str) -> None:
        """Pull a container image.

        Args:
            image: Image to pull.
        """
        if not image_exists(image):
            run_podman("pull", image, capture=False)
