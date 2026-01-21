"""Hash computation for config caching."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_config_hash(
    config_file: Path | None,
    dockerfile: Path | None,
    base_image: str | None,
    entrypoint: Path,
    workspace: Path | None = None,
    pip_install: bool | str = False,
) -> str:
    """Compute a deterministic hash of the configuration.

    This must match the bash compute_config_hash() function exactly.
    The bash version concatenates file contents and uses sha256sum.

    Args:
        config_file: Path to config file (devcontainer.json or paude.json).
        dockerfile: Path to Dockerfile if specified in config.
        base_image: Base image name if specified.
        entrypoint: Path to entrypoint.sh.
        workspace: Path to workspace directory (for pip_install hash).
        pip_install: Whether pip_install is enabled (bool or string command).

    Returns:
        12-character hash string.
    """
    hash_input = ""

    # Include config file content
    if config_file and config_file.exists():
        hash_input += config_file.read_text()

    # Include Dockerfile content if referenced
    if dockerfile and dockerfile.exists():
        hash_input += dockerfile.read_text()

    # Include base image name (for image-only configs)
    if base_image:
        hash_input += base_image

    # Include entrypoint.sh content
    if entrypoint.exists():
        hash_input += entrypoint.read_text()

    # Include dependency files when pip_install is enabled
    if pip_install and workspace:
        for dep_file in ["pyproject.toml", "requirements.txt"]:
            dep_path = workspace / dep_file
            if dep_path.exists():
                hash_input += dep_path.read_text()

    # Generate hash - match bash behavior exactly
    # bash: echo "$hash_input" | sha256sum | cut -c1-12
    # The echo adds a trailing newline
    hash_bytes = (hash_input + "\n").encode("utf-8")
    hash_hex = hashlib.sha256(hash_bytes).hexdigest()[:12]

    return hash_hex


def is_image_stale(image_tag: str) -> bool:
    """Check if the image with the given tag exists.

    Args:
        image_tag: Full image tag including hash.

    Returns:
        True if image is stale (doesn't exist), False if fresh.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["podman", "image", "exists", image_tag],
            capture_output=True,
            check=False,
        )
        return result.returncode != 0
    except FileNotFoundError:
        # podman not installed
        return True
