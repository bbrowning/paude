"""Image management for paude containers."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from paude import __version__
from paude.config.claude_layer import generate_claude_layer_dockerfile
from paude.config.models import PaudeConfig
from paude.container.podman import image_exists, run_podman
from paude.hash import compute_config_hash, compute_content_hash


@dataclass
class BuildContext:
    """Build context for container image builds.

    Attributes:
        context_dir: Path to the build context directory.
        dockerfile_path: Path to the Dockerfile within the context.
        config_hash: Hash of the configuration for image tagging.
        base_image: Base image to use (for build args).
    """

    context_dir: Path
    dockerfile_path: Path
    config_hash: str
    base_image: str


def prepare_build_context(
    config: PaudeConfig,
    workspace: Path | None = None,
    script_dir: Path | None = None,
    platform: str | None = None,
    for_remote_build: bool = False,
) -> BuildContext:
    """Prepare a build context directory for container image builds.

    This function creates a temporary directory containing all files needed
    to build a container image, including the Dockerfile, entrypoints, and
    workspace source (for pip_install). The context can be used by both
    local Podman builds and OpenShift binary builds.

    Args:
        config: Parsed paude configuration.
        workspace: Path to workspace directory (for pip_install).
        script_dir: Path to paude script directory (for dev mode).
        platform: Target platform (for image tagging).
        for_remote_build: If True, skip local podman operations and use
            registry-accessible base images. Used for OpenShift binary builds.

    Returns:
        BuildContext with paths to the context directory and Dockerfile.

    Note:
        The caller is responsible for cleaning up the context_dir when done.
        Use shutil.rmtree(context.context_dir) or a context manager.
    """
    import sys

    base_path = Path(__file__).parent.parent.parent.parent
    entrypoint = base_path / "containers" / "paude" / "entrypoint.sh"
    if script_dir:
        entrypoint = script_dir / "containers" / "paude" / "entrypoint.sh"

    config_hash = compute_config_hash(
        config.config_file,
        config.dockerfile,
        config.base_image,
        entrypoint,
        workspace=workspace,
        pip_install=config.pip_install,
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="paude-build-"))

    base_image: str
    using_default_paude_image = False

    if config.dockerfile:
        if not config.dockerfile.exists():
            shutil.rmtree(tmpdir)
            raise FileNotFoundError(f"Dockerfile not found: {config.dockerfile}")

        if for_remote_build:
            # For remote builds, we can't build user's Dockerfile locally first.
            # Use a multi-stage build: stage 1 is user's Dockerfile, stage 2 adds
            # paude requirements (Claude, etc.) on top.
            from paude.config.dockerfile import generate_workspace_dockerfile

            user_dockerfile = config.dockerfile.read_text()
            print(f"  → Using user Dockerfile: {config.dockerfile}", file=sys.stderr)

            # Copy build context files first
            build_context = config.build_context or config.dockerfile.parent
            for item in build_context.iterdir():
                if item.name == "Dockerfile":
                    continue  # Will be generated
                dest = tmpdir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

            # Create multi-stage Dockerfile:
            # Stage 1: User's original Dockerfile (as "user-base")
            # Stage 2: Add paude requirements on top
            stage1 = user_dockerfile.rstrip()
            # Add AS user-base to the FROM line of user's Dockerfile
            stage1_lines = stage1.split("\n")
            for i, line in enumerate(stage1_lines):
                if line.strip().upper().startswith("FROM "):
                    # Add AS user-base to the first FROM
                    if " AS " not in line.upper():
                        stage1_lines[i] = line + " AS user-base"
                    break
            stage1 = "\n".join(stage1_lines)

            # Stage 2 uses generate_workspace_dockerfile but with user-base as base
            stage2 = generate_workspace_dockerfile(config)
            # Replace the ARG/FROM with FROM user-base
            stage2 = stage2.replace(
                "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}",
                "FROM user-base",
            )

            combined_dockerfile = (
                f"{stage1}\n\n"
                f"# === Stage 2: Add paude requirements ===\n"
                f"{stage2}"
            )
            (tmpdir / "Dockerfile").write_text(combined_dockerfile)

            # Copy entrypoints for stage 2
            base_path = Path(__file__).parent.parent.parent.parent
            entrypoint = base_path / "containers" / "paude" / "entrypoint.sh"
            if script_dir:
                entrypoint = script_dir / "containers" / "paude" / "entrypoint.sh"

            entrypoint_dest = tmpdir / "entrypoint.sh"
            if entrypoint.exists():
                content = entrypoint.read_text().replace("\r\n", "\n")
                entrypoint_dest.write_text(content, newline="\n")
            else:
                entrypoint_dest.write_text(
                    "#!/bin/bash\nexec claude \"$@\"\n", newline="\n"
                )
            entrypoint_dest.chmod(0o755)

            entrypoint_session = entrypoint.parent / "entrypoint-session.sh"
            entrypoint_session_dest = tmpdir / "entrypoint-session.sh"
            if entrypoint_session.exists():
                content = entrypoint_session.read_text().replace("\r\n", "\n")
                entrypoint_session_dest.write_text(content, newline="\n")
                entrypoint_session_dest.chmod(0o755)

            # Copy workspace source for pip_install (stage 2 needs these files)
            if config.pip_install and workspace:
                print("  → Copying workspace for pip install...", file=sys.stderr)
                for item in workspace.iterdir():
                    if item.name.startswith("."):
                        continue
                    dest = tmpdir / item.name
                    if dest.exists():
                        # Don't overwrite files from Dockerfile context
                        continue
                    if item.is_dir():
                        shutil.copytree(
                            item,
                            dest,
                            ignore=shutil.ignore_patterns(
                                "__pycache__",
                                "*.pyc",
                                ".git",
                                ".venv",
                                "venv",
                                "*.egg-info",
                                "build",
                                "dist",
                            ),
                        )
                    else:
                        shutil.copy2(item, dest)

            base_image = "user-base"
            print("  → Adding paude requirements (multi-stage)...", file=sys.stderr)

            return BuildContext(
                context_dir=tmpdir,
                dockerfile_path=tmpdir / "Dockerfile",
                config_hash=config_hash,
                base_image=base_image,
            )
        else:
            user_image = f"paude-user-base:{config_hash}"
            build_context = config.build_context or config.dockerfile.parent
            print(f"  → Building from: {config.dockerfile}", file=sys.stderr)

            user_build_args = dict(config.build_args)

            cmd = ["build", "-f", str(config.dockerfile), "-t", user_image]
            if platform:
                cmd.extend(["--platform", platform])
            if user_build_args:
                for key, value in user_build_args.items():
                    cmd.extend(["--build-arg", f"{key}={value}"])
            cmd.append(str(build_context))
            run_podman(*cmd, capture=False)

            base_image = user_image
            print("  → Adding paude requirements...", file=sys.stderr)
    elif config.base_image:
        base_image = config.base_image
        print(f"  → Using base: {base_image}", file=sys.stderr)
    else:
        registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
        if for_remote_build:
            # For remote builds, always use registry image (skip dev mode local builds)
            base_image = f"{registry}/paude-base-centos9:{__version__}"
            print(f"  → Using registry image: {base_image}", file=sys.stderr)
        else:
            dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
            if dev_mode and script_dir:
                if platform:
                    arch = platform.split("/")[-1]
                    base_image = f"paude-base-centos9:latest-{arch}"
                else:
                    base_image = "paude-base-centos9:latest"
                if not image_exists(base_image):
                    print(f"Building {base_image} image...", file=sys.stderr)
                    dockerfile = script_dir / "containers" / "paude" / "Dockerfile"
                    context = script_dir / "containers" / "paude"
                    cmd = ["build", "-f", str(dockerfile), "-t", base_image]
                    if platform:
                        cmd.extend(["--platform", platform])
                    cmd.append(str(context))
                    run_podman(*cmd, capture=False)
            else:
                base_image = f"{registry}/paude-base-centos9:{__version__}"
                if not image_exists(base_image):
                    print(f"Pulling {base_image}...", file=sys.stderr)
                    run_podman("pull", base_image, capture=False)
        using_default_paude_image = True
        print(f"  → Using default paude image: {base_image}", file=sys.stderr)

    if using_default_paude_image:
        from paude.config.dockerfile import generate_pip_install_dockerfile

        # Always include Claude installation since the base image doesn't have Claude
        # (due to licensing restrictions that prohibit redistribution)
        dockerfile_content = generate_pip_install_dockerfile(
            config, include_claude_install=True
        )
    else:
        from paude.config.dockerfile import generate_workspace_dockerfile

        dockerfile_content = generate_workspace_dockerfile(config)

    if config.features:
        from paude.features.installer import generate_features_dockerfile

        features_block = generate_features_dockerfile(config.features)
        if features_block:
            # Replace only FIRST "\nUSER paude" - features run as root.
            # count=1 avoids duplicating when Dockerfile has multiple USER paude
            dockerfile_content = dockerfile_content.replace(
                "\nUSER paude",
                f"{features_block}\nUSER paude",
                1,
            )

    # Replace ARG BASE_IMAGE / FROM ${BASE_IMAGE} with actual base image
    # This makes the Dockerfile self-contained for OpenShift binary builds
    dockerfile_content = dockerfile_content.replace(
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}",
        f"FROM {base_image}",
    )

    dockerfile_path = tmpdir / "Dockerfile"
    dockerfile_path.write_text(dockerfile_content)

    if not using_default_paude_image:
        entrypoint_dest = tmpdir / "entrypoint.sh"
        if entrypoint.exists():
            content = entrypoint.read_text().replace("\r\n", "\n")
            entrypoint_dest.write_text(content, newline="\n")
        else:
            entrypoint_dest.write_text(
                "#!/bin/bash\nexec claude \"$@\"\n", newline="\n"
            )
        entrypoint_dest.chmod(0o755)

        entrypoint_session = entrypoint.parent / "entrypoint-session.sh"
        entrypoint_session_dest = tmpdir / "entrypoint-session.sh"
        if entrypoint_session.exists():
            content = entrypoint_session.read_text().replace("\r\n", "\n")
            entrypoint_session_dest.write_text(content, newline="\n")
            entrypoint_session_dest.chmod(0o755)

    if config.features:
        from paude.features.downloader import FEATURE_CACHE_DIR

        if FEATURE_CACHE_DIR.exists():
            features_dest = tmpdir / "features"
            shutil.copytree(FEATURE_CACHE_DIR, features_dest)

    if config.pip_install and workspace:
        print("  → Copying workspace for pip install...", file=sys.stderr)
        for item in workspace.iterdir():
            if item.name.startswith("."):
                continue
            dest = tmpdir / item.name
            if item.is_dir():
                shutil.copytree(
                    item,
                    dest,
                    ignore=shutil.ignore_patterns(
                        "__pycache__",
                        "*.pyc",
                        ".git",
                        ".venv",
                        "venv",
                        "*.egg-info",
                        "build",
                        "dist",
                    ),
                )
            else:
                shutil.copy2(item, dest)

    dockerignore_content = """.venv
venv
__pycache__
*.pyc
.git
node_modules
*.egg-info
build
dist
"""
    (tmpdir / ".dockerignore").write_text(dockerignore_content)

    return BuildContext(
        context_dir=tmpdir,
        dockerfile_path=dockerfile_path,
        config_hash=config_hash,
        base_image=base_image,
    )


class ImageManager:
    """Manages container images for paude."""

    def __init__(
        self,
        script_dir: Path | None = None,
        platform: str | None = None,
    ):
        """Initialize the image manager.

        Args:
            script_dir: Path to the paude script directory (for dev mode).
            platform: Target platform (e.g., "linux/amd64"). If None, uses native arch.
        """
        self.script_dir = script_dir
        self.dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        self.registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
        self.version = __version__
        self.platform = platform

    def ensure_default_image(self) -> str:
        """Ensure the default paude image is available.

        This builds a two-layer image:
        1. Base image (no Claude) - built locally in dev mode or pulled from registry
        2. Runtime image (with Claude) - always built locally

        Claude Code is installed at user-side build time (not in the published
        image) due to licensing restrictions that prohibit redistribution.

        Returns:
            Image tag to use (the runtime image with Claude installed).
        """
        base_tag = self._ensure_base_image()
        return self._ensure_runtime_image(base_tag)

    def _ensure_base_image(self) -> str:
        """Ensure the base paude image (without Claude Code) is available.

        Returns:
            Base image tag.
        """
        import sys

        if self.dev_mode and self.script_dir:
            # Build locally in dev mode
            if self.platform:
                arch = self.platform.split("/")[-1]
                tag = f"paude-base-centos9:latest-{arch}"
            else:
                tag = "paude-base-centos9:latest"
            if not image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "paude" / "Dockerfile"
                context = self.script_dir / "containers" / "paude"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            # Pull from registry with version tag
            tag = f"{self.registry}/paude-base-centos9:{self.version}"
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

    def _ensure_runtime_image(self, base_image: str) -> str:
        """Ensure the runtime image (with Claude Code installed) is available.

        Args:
            base_image: The base image tag to build on top of.

        Returns:
            Runtime image tag with Claude Code installed.
        """
        import sys

        layer_content = generate_claude_layer_dockerfile()
        layer_hash = compute_content_hash(
            base_image.encode(),
            self.version.encode(),
            layer_content.encode(),
        )

        if self.platform:
            arch = self.platform.split("/")[-1]
            runtime_tag = f"paude-runtime:{layer_hash[:12]}-{arch}"
        else:
            runtime_tag = f"paude-runtime:{layer_hash[:12]}"

        if image_exists(runtime_tag):
            print(f"Using cached runtime image: {runtime_tag}", file=sys.stderr)
            return runtime_tag

        # First run: explain why we're building locally
        print("Installing Claude Code (first run only)...", file=sys.stderr)

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(layer_content)

            build_args = {"BASE_IMAGE": base_image}
            try:
                self.build_image(
                    dockerfile_path, runtime_tag, Path(tmpdir), build_args
                )
            except Exception:
                print(
                    "\nClaude Code installation failed. This usually means:\n"
                    "  - Network connectivity issues (check your connection)\n"
                    "  - Podman machine not running (run 'podman machine start')\n"
                    "  - Disk space issues\n",
                    file=sys.stderr,
                )
                raise

        print("Claude Code installed successfully.", file=sys.stderr)
        return runtime_tag

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
        # Use platform-specific tag to avoid arch conflicts
        if self.platform:
            arch = self.platform.split("/")[-1]  # e.g., "linux/amd64" -> "amd64"
            tag = f"paude-workspace:{config_hash}-{arch}"
        else:
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
            using_default_paude_image = False
            print("  → Adding paude requirements...", file=sys.stderr)
        elif config.base_image:
            base_image = config.base_image
            using_default_paude_image = False
            print(f"  → Using base: {base_image}", file=sys.stderr)
        else:
            # No custom base specified - use the default paude image
            base_image = self.ensure_default_image()
            using_default_paude_image = True
            print(f"  → Using default paude image: {base_image}", file=sys.stderr)

        # Generate the workspace Dockerfile
        if using_default_paude_image:
            # Simpler Dockerfile - just add pip_install layer on top of complete image
            from paude.config.dockerfile import generate_pip_install_dockerfile

            dockerfile_content = generate_pip_install_dockerfile(config)
        else:
            # Full Dockerfile - install all paude requirements on top of user's base
            from paude.config.dockerfile import generate_workspace_dockerfile

            dockerfile_content = generate_workspace_dockerfile(config)

        # Add features if present (matches bash behavior)
        if config.features:
            from paude.features.installer import generate_features_dockerfile

            features_block = generate_features_dockerfile(config.features)
            if features_block:
                # Replace only FIRST "\nUSER paude" - features run as root
                dockerfile_content = dockerfile_content.replace(
                    "\nUSER paude",
                    f"{features_block}\nUSER paude",
                    1,
                )

        # Write temporary Dockerfile
        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(dockerfile_content)

            # Copy entrypoints only when not using default paude image
            # (default image already has entrypoints installed)
            if not using_default_paude_image:
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

                # Copy session entrypoint for persistent sessions (Podman and OpenShift)
                entrypoint_session = entrypoint.parent / "entrypoint-session.sh"
                entrypoint_session_dest = Path(tmpdir) / "entrypoint-session.sh"
                if entrypoint_session.exists():
                    content = entrypoint_session.read_text().replace("\r\n", "\n")
                    entrypoint_session_dest.write_text(content, newline="\n")
                    entrypoint_session_dest.chmod(0o755)

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
            # Build locally in dev mode
            # Use platform-specific tag to avoid arch conflicts
            if self.platform:
                arch = self.platform.split("/")[-1]  # e.g., "linux/amd64" -> "amd64"
                tag = f"paude-proxy-centos9:latest-{arch}"
            else:
                tag = "paude-proxy-centos9:latest"
            if not image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "proxy" / "Dockerfile"
                context = self.script_dir / "containers" / "proxy"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            # Pull from registry with version tag (matches bash)
            tag = f"{self.registry}/paude-proxy-centos9:{self.version}"
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
    ) -> None:
        """Build a container image.

        Args:
            dockerfile: Path to Dockerfile.
            tag: Image tag.
            context: Build context directory.
            build_args: Optional build arguments.
        """
        cmd = ["build", "-f", str(dockerfile), "-t", tag]

        # Use platform from ImageManager if specified
        if self.platform:
            cmd.extend(["--platform", self.platform])
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        cmd.append(str(context))
        run_podman(*cmd, capture=False)
