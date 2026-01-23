"""Image management for paude containers."""

from __future__ import annotations

import os
from pathlib import Path

from paude import __version__
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
        self.version = __version__

    def ensure_default_image(self) -> str:
        """Ensure the default paude image is available.

        Returns:
            Image tag to use.
        """
        import sys

        if self.dev_mode and self.script_dir:
            # Build locally in dev mode (matches bash: paude:latest)
            tag = "paude:latest"
            if not image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "paude" / "Dockerfile"
                context = self.script_dir / "containers" / "paude"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            # Pull from registry with version tag (matches bash)
            tag = f"{self.registry}/paude:{self.version}"
            if not image_exists(tag):
                print(f"Pulling {tag}...", file=sys.stderr)
                try:
                    run_podman("pull", tag, capture=False)
                except Exception:
                    print(
                        "Check your network connection or run 'podman login' "
                        "if authentication is required.",
                        file=sys.stderr,
                    )
                    raise
            return tag

    def ensure_custom_image(
        self,
        config: PaudeConfig,
        force_rebuild: bool = False,
        workspace: Path | None = None,
    ) -> str:
        """Ensure a custom workspace image is available.

        Args:
            config: Parsed paude configuration.
            force_rebuild: Force rebuild even if image exists.
            workspace: Path to the workspace directory (for pip_install).

        Returns:
            Image tag to use.
        """
        import shutil
        import sys
        import tempfile

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
            workspace=workspace,
            pip_install=config.pip_install,
        )
        tag = f"paude-workspace:{config_hash}"

        # Check if we need to build
        if not force_rebuild and image_exists(tag):
            print(f"Using cached workspace image: {tag}", file=sys.stderr)
            return tag

        print("Building workspace image...", file=sys.stderr)

        # Determine the base image to use
        base_image: str

        if config.dockerfile:
            # Verify Dockerfile exists (matches bash behavior)
            if not config.dockerfile.exists():
                raise FileNotFoundError(
                    f"Dockerfile not found: {config.dockerfile}"
                )

            # Build user's Dockerfile first to create intermediate image
            user_image = f"paude-user-base:{config_hash}"
            build_context = config.build_context or config.dockerfile.parent
            print(f"  → Building from: {config.dockerfile}", file=sys.stderr)

            # Build user's Dockerfile
            user_build_args = dict(config.build_args)
            self.build_image(
                config.dockerfile, user_image, build_context, user_build_args
            )
            base_image = user_image
            print("  → Adding paude requirements...", file=sys.stderr)
        elif config.base_image:
            base_image = config.base_image
            print(f"  → Using base: {base_image}", file=sys.stderr)
        else:
            base_image = "debian:bookworm-slim"

        # Generate and build the workspace Dockerfile
        from paude.config.dockerfile import generate_workspace_dockerfile

        dockerfile_content = generate_workspace_dockerfile(config)

        # Add features if present (matches bash behavior)
        if config.features:
            from paude.features.installer import generate_features_dockerfile

            features_block = generate_features_dockerfile(config.features)
            if features_block:
                # Insert features before "USER paude" line
                dockerfile_content = dockerfile_content.replace(
                    "\nUSER paude",
                    f"{features_block}\nUSER paude",
                )

        # Write temporary Dockerfile
        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(dockerfile_content)

            # Copy entrypoints (ensure Unix line endings for Linux containers)
            entrypoint_dest = Path(tmpdir) / "entrypoint.sh"
            if entrypoint.exists():
                content = entrypoint.read_text().replace("\r\n", "\n")
                entrypoint_dest.write_text(content, newline="\n")
            else:
                # Minimal fallback
                entrypoint_dest.write_text(
                    "#!/bin/bash\nexec claude \"$@\"\n", newline="\n"
                )
            entrypoint_dest.chmod(0o755)

            # Copy tmux entrypoint for OpenShift session persistence
            entrypoint_tmux = entrypoint.parent / "entrypoint-tmux.sh"
            entrypoint_tmux_dest = Path(tmpdir) / "entrypoint-tmux.sh"
            if entrypoint_tmux.exists():
                content = entrypoint_tmux.read_text().replace("\r\n", "\n")
                entrypoint_tmux_dest.write_text(content, newline="\n")
                entrypoint_tmux_dest.chmod(0o755)

            # Copy features to build context if present
            if config.features:
                from paude.features.downloader import FEATURE_CACHE_DIR

                if FEATURE_CACHE_DIR.exists():
                    features_dest = Path(tmpdir) / "features"
                    shutil.copytree(FEATURE_CACHE_DIR, features_dest)

            # Copy workspace source for pip_install
            if config.pip_install and workspace:
                print("  → Copying workspace for pip install...", file=sys.stderr)
                for item in workspace.iterdir():
                    if item.name.startswith("."):
                        continue
                    dest = Path(tmpdir) / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", ".git", ".venv", "venv",
                            "*.egg-info", "build", "dist"
                        ))
                    else:
                        shutil.copy2(item, dest)

            # Build with the determined base image
            build_args = {"BASE_IMAGE": base_image}
            self.build_image(dockerfile_path, tag, Path(tmpdir), build_args)

        print(f"Build complete (cached as {tag})", file=sys.stderr)
        return tag

    def ensure_proxy_image(self) -> str:
        """Ensure the proxy image is available.

        Returns:
            Image tag to use.
        """
        import sys

        if self.dev_mode and self.script_dir:
            # Build locally in dev mode (matches bash: paude-proxy:latest)
            tag = "paude-proxy:latest"
            if not image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "proxy" / "Dockerfile"
                context = self.script_dir / "containers" / "proxy"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            # Pull from registry with version tag (matches bash)
            tag = f"{self.registry}/paude-proxy:{self.version}"
            if not image_exists(tag):
                print(f"Pulling {tag}...", file=sys.stderr)
                try:
                    run_podman("pull", tag, capture=False)
                except Exception:
                    print(
                        "Check your network connection or run 'podman login' "
                        "if authentication is required.",
                        file=sys.stderr,
                    )
                    raise
            return tag

    def build_image(
        self,
        dockerfile: Path,
        tag: str,
        context: Path,
        build_args: dict[str, str] | None = None,
        platform: str | None = "linux/amd64",
    ) -> None:
        """Build a container image.

        Args:
            dockerfile: Path to Dockerfile.
            tag: Image tag.
            context: Build context directory.
            build_args: Optional build arguments.
            platform: Target platform (default: linux/amd64 for compatibility).
        """
        cmd = ["build", "-f", str(dockerfile), "-t", tag]
        if platform:
            cmd.extend(["--platform", platform])
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        cmd.append(str(context))
        run_podman(*cmd, capture=False)
