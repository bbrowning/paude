"""Build context preparation for container image builds."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from paude import __version__
from paude.config.models import FeatureSpec, PaudeConfig
from paude.container.podman import image_exists, run_podman
from paude.hash import compute_config_hash

if TYPE_CHECKING:
    from paude.agents.base import Agent


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


def resolve_entrypoint(script_dir: Path | None) -> Path:
    """Resolve the entrypoint.sh path based on script directory."""
    base_path = Path(__file__).parent.parent.parent.parent
    if script_dir:
        return script_dir / "containers" / "paude" / "entrypoint.sh"
    return base_path / "containers" / "paude" / "entrypoint.sh"


def copy_entrypoints(entrypoint: Path, dest_dir: Path) -> None:
    """Copy entrypoint scripts to build context with Unix line endings."""
    entrypoint_dest = dest_dir / "entrypoint.sh"
    if entrypoint.exists():
        content = entrypoint.read_text().replace("\r\n", "\n")
        entrypoint_dest.write_text(content, newline="\n")
    else:
        entrypoint_dest.write_text('#!/bin/bash\nexec claude "$@"\n', newline="\n")
    entrypoint_dest.chmod(0o755)

    entrypoint_session = entrypoint.parent / "entrypoint-session.sh"
    entrypoint_session_dest = dest_dir / "entrypoint-session.sh"
    if entrypoint_session.exists():
        content = entrypoint_session.read_text().replace("\r\n", "\n")
        entrypoint_session_dest.write_text(content, newline="\n")
        entrypoint_session_dest.chmod(0o755)

    tmux_conf = entrypoint.parent / "tmux.conf"
    if tmux_conf.exists():
        content = tmux_conf.read_text().replace("\r\n", "\n")
        (dest_dir / "tmux.conf").write_text(content, newline="\n")


def inject_features(dockerfile_content: str, features: list[FeatureSpec] | None) -> str:
    """Inject devcontainer features block into Dockerfile content."""
    if not features:
        return dockerfile_content

    from paude.features.installer import generate_features_dockerfile

    features_block = generate_features_dockerfile(features)
    if features_block:
        # Replace only FIRST "\nUSER paude" - features run as root.
        # count=1 avoids duplicating when Dockerfile has multiple USER paude
        dockerfile_content = dockerfile_content.replace(
            "\nUSER paude",
            f"{features_block}\nUSER paude",
            1,
        )
    return dockerfile_content


def copy_features_cache(dest_dir: Path) -> None:
    """Copy downloaded features to build context if present."""
    from paude.features.downloader import FEATURE_CACHE_DIR

    if FEATURE_CACHE_DIR.exists():
        features_dest = dest_dir / "features"
        shutil.copytree(FEATURE_CACHE_DIR, features_dest)


def generate_dockerfile_content(
    config: PaudeConfig,
    using_default_paude_image: bool,
    include_claude_install: bool = False,
    agent: Agent | None = None,
) -> str:
    """Generate Dockerfile content with features injected."""
    if using_default_paude_image:
        from paude.config.dockerfile import generate_pip_install_dockerfile

        content = generate_pip_install_dockerfile(
            config, include_claude_install=include_claude_install, agent=agent
        )
    else:
        from paude.config.dockerfile import generate_workspace_dockerfile

        content = generate_workspace_dockerfile(config, agent=agent)

    return inject_features(content, config.features)


def _write_dockerignore(dest_dir: Path) -> None:
    """Write .dockerignore to build context."""
    content = """.venv
venv
__pycache__
*.pyc
.git
node_modules
*.egg-info
build
dist
"""
    (dest_dir / ".dockerignore").write_text(content)


def _add_stage_alias(user_dockerfile: str) -> str:
    """Add 'AS user-base' alias to the first FROM line if not already aliased."""
    lines = user_dockerfile.rstrip().split("\n")
    for i, line in enumerate(lines):
        if line.strip().upper().startswith("FROM "):
            if " AS " not in line.upper():
                lines[i] = line + " AS user-base"
            break
    return "\n".join(lines)


def _prepare_remote_multistage(
    config: PaudeConfig,
    tmpdir: Path,
    entrypoint: Path,
    config_hash: str,
    agent: Agent | None = None,
) -> BuildContext:
    """Prepare build context for remote multi-stage builds with user Dockerfile."""
    import sys

    from paude.config.dockerfile import generate_workspace_dockerfile

    dockerfile = config.dockerfile
    if dockerfile is None:
        raise ValueError("config.dockerfile must be set for remote multi-stage builds")
    user_dockerfile = dockerfile.read_text()
    print(f"  → Using user Dockerfile: {dockerfile}", file=sys.stderr)

    # Copy build context files (excluding Dockerfile which will be generated)
    build_context = config.build_context or dockerfile.parent
    for item in build_context.iterdir():
        if item.name == "Dockerfile":
            continue
        dest = tmpdir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    # Stage 1: User's original Dockerfile (as "user-base")
    stage1 = _add_stage_alias(user_dockerfile)
    # Stage 2: Add paude requirements on top
    stage2 = generate_workspace_dockerfile(config, agent=agent)
    stage2 = stage2.replace(
        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}",
        "FROM user-base",
    )
    combined = f"{stage1}\n\n# === Stage 2: Add paude requirements ===\n{stage2}"
    (tmpdir / "Dockerfile").write_text(combined)

    copy_entrypoints(entrypoint, tmpdir)

    print("  → Adding paude requirements (multi-stage)...", file=sys.stderr)
    return BuildContext(
        context_dir=tmpdir,
        dockerfile_path=tmpdir / "Dockerfile",
        config_hash=config_hash,
        base_image="user-base",
    )


def _build_user_image_locally(
    config: PaudeConfig,
    config_hash: str,
    platform: str | None,
) -> str:
    """Build user's Dockerfile locally and return the intermediate image tag."""
    import sys

    user_image = f"paude-user-base:{config_hash}"
    dockerfile = config.dockerfile
    if dockerfile is None:
        raise ValueError("config.dockerfile must be set for local image builds")
    build_context = config.build_context or dockerfile.parent
    print(f"  → Building from: {dockerfile}", file=sys.stderr)

    user_build_args = dict(config.build_args)
    cmd = ["build", "-f", str(dockerfile), "-t", user_image]
    if platform:
        cmd.extend(["--platform", platform])
    if user_build_args:
        for key, value in user_build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])
    cmd.append(str(build_context))
    run_podman(*cmd, capture=False)

    print("  → Adding paude requirements...", file=sys.stderr)
    return user_image


def _resolve_default_base(
    script_dir: Path | None,
    platform: str | None,
    for_remote_build: bool,
) -> str:
    """Resolve the default paude base image (no custom Dockerfile or base_image)."""
    import sys

    registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")

    if for_remote_build:
        base_image = f"{registry}/paude-base-centos10:{__version__}"
        print(f"  → Using registry image: {base_image}", file=sys.stderr)
        return base_image

    dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
    if dev_mode and script_dir:
        if platform:
            arch = platform.split("/")[-1]
            base_image = f"paude-base-centos10:latest-{arch}"
        else:
            base_image = "paude-base-centos10:latest"
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
        base_image = f"{registry}/paude-base-centos10:{__version__}"
        if not image_exists(base_image):
            print(f"Pulling {base_image}...", file=sys.stderr)
            run_podman("pull", base_image, capture=False)

    return base_image


def prepare_build_context(
    config: PaudeConfig,
    workspace: Path | None = None,
    script_dir: Path | None = None,
    platform: str | None = None,
    for_remote_build: bool = False,
    agent: Agent | None = None,
) -> BuildContext:
    """Prepare a build context directory for container image builds.

    This function creates a temporary directory containing all files needed
    to build a container image, including the Dockerfile and entrypoints.
    The context can be used by both local Podman builds and OpenShift binary builds.

    Args:
        config: Parsed paude configuration.
        workspace: Deprecated, ignored. Code sync is done via git push.
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

    entrypoint = resolve_entrypoint(script_dir)
    agent_name = agent.config.name if agent else None
    config_hash = compute_config_hash(
        config.config_file,
        config.dockerfile,
        config.base_image,
        entrypoint,
        __version__,
        agent_name=agent_name,
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="paude-build-"))

    if config.dockerfile:
        if not config.dockerfile.exists():
            shutil.rmtree(tmpdir)
            raise FileNotFoundError(f"Dockerfile not found: {config.dockerfile}")
        if for_remote_build:
            return _prepare_remote_multistage(
                config, tmpdir, entrypoint, config_hash, agent=agent
            )
        base_image = _build_user_image_locally(config, config_hash, platform)
        using_default_paude_image = False
    elif config.base_image:
        base_image = config.base_image
        using_default_paude_image = False
        print(f"  → Using base: {base_image}", file=sys.stderr)
    else:
        base_image = _resolve_default_base(script_dir, platform, for_remote_build)
        using_default_paude_image = True
        print(f"  → Using default paude image: {base_image}", file=sys.stderr)

    dockerfile_content = generate_dockerfile_content(
        config, using_default_paude_image, include_claude_install=True, agent=agent
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
        copy_entrypoints(entrypoint, tmpdir)

    if config.features:
        copy_features_cache(tmpdir)

    _write_dockerignore(tmpdir)

    return BuildContext(
        context_dir=tmpdir,
        dockerfile_path=dockerfile_path,
        config_hash=config_hash,
        base_image=base_image,
    )
