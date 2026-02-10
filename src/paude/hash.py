"""Hash computation for config caching."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_config_hash(
    config_file: Path | None,
    dockerfile: Path | None,
    base_image: str | None,
    entrypoint: Path,
    version: str,
) -> str:
    """Compute a deterministic hash of the configuration.

    Includes the paude version so that upgrading paude triggers a rebuild
    even when no other inputs have changed.

    Args:
        config_file: Path to config file (devcontainer.json or paude.json).
        dockerfile: Path to Dockerfile if specified in config.
        base_image: Base image name if specified.
        entrypoint: Path to entrypoint.sh.
        version: The paude package version string.

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

    # Include version to trigger rebuilds on upgrade
    hash_input += version

    hash_bytes = (hash_input + "\n").encode("utf-8")
    hash_hex = hashlib.sha256(hash_bytes).hexdigest()[:12]

    return hash_hex


def compute_content_hash(*content: bytes) -> str:
    """Compute a hash from arbitrary byte content.

    Args:
        *content: Variable number of bytes objects to hash together.

    Returns:
        Full hex digest of the combined content hash.
    """
    hasher = hashlib.sha256()
    for item in content:
        hasher.update(item)
    return hasher.hexdigest()


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
