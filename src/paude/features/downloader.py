"""Feature downloading for dev container features."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

FEATURE_CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "paude" / "features"


def download_feature(feature_url: str) -> Path:
    """Download a dev container feature from ghcr.io.

    Args:
        feature_url: Feature URL (e.g., "ghcr.io/devcontainers/features/python:1").

    Returns:
        Path to extracted feature directory.

    Raises:
        RuntimeError: If download fails.
    """
    # Compute cache key
    feature_hash = hashlib.sha256((feature_url + "\n").encode()).hexdigest()[:12]
    feature_dir = FEATURE_CACHE_DIR / feature_hash

    # Check cache
    if feature_dir.exists() and (feature_dir / "install.sh").exists():
        return feature_dir

    feature_dir.mkdir(parents=True, exist_ok=True)

    print(f"  → Downloading feature: {feature_url}", file=__import__("sys").stderr)

    # Try ORAS first
    if shutil.which("oras"):
        result = subprocess.run(
            ["oras", "pull", feature_url, "-o", str(feature_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(feature_dir, ignore_errors=True)
            msg = f"Failed to download feature {feature_url}: {result.stderr}"
            raise RuntimeError(msg)
    elif shutil.which("skopeo"):
        # Try skopeo
        tmp_tar = feature_dir / "feature.tar"
        result = subprocess.run(
            ["skopeo", "copy", f"docker://{feature_url}", f"oci-archive:{tmp_tar}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            shutil.rmtree(feature_dir, ignore_errors=True)
            msg = f"Failed to download feature {feature_url}: {result.stderr}"
            raise RuntimeError(msg)
        subprocess.run(
            ["tar", "-xf", str(tmp_tar), "-C", str(feature_dir)], check=True
        )
        tmp_tar.unlink()
    else:
        # Fallback: curl-based download
        # This is a simplified approach for ghcr.io
        _download_with_curl(feature_url, feature_dir)

    # Verify install.sh exists
    install_script = feature_dir / "install.sh"
    if not install_script.exists():
        shutil.rmtree(feature_dir, ignore_errors=True)
        raise RuntimeError(f"Feature missing install.sh: {feature_url}")

    install_script.chmod(0o755)
    return feature_dir


def _download_with_curl(feature_url: str, feature_dir: Path) -> None:
    """Download feature using curl (fallback method).

    Args:
        feature_url: Feature URL.
        feature_dir: Directory to extract to.

    Raises:
        RuntimeError: If download fails.
    """
    import json
    import urllib.request

    # Parse the feature URL
    # Format: ghcr.io/devcontainers/features/python:1
    parts = feature_url.split("/", 1)
    registry = parts[0]  # ghcr.io
    path_and_tag = parts[1]  # devcontainers/features/python:1
    if ":" in path_and_tag:
        path, tag = path_and_tag.rsplit(":", 1)
    else:
        path, tag = path_and_tag, "latest"

    # Get anonymous token
    token_url = f"https://{registry}/token?scope=repository:{path}:pull"
    try:
        with urllib.request.urlopen(token_url) as response:
            token_data = json.loads(response.read().decode())
            token = token_data.get("token", "")
    except Exception as e:
        raise RuntimeError(f"Failed to get token for {feature_url}: {e}") from e

    if not token:
        raise RuntimeError(f"Failed to get token for {feature_url}")

    # Get manifest
    manifest_url = f"https://{registry}/v2/{path}/manifests/{tag}"
    req = urllib.request.Request(
        manifest_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.manifest.v1+json",
        },
    )
    try:
        with urllib.request.urlopen(req) as response:
            manifest = json.loads(response.read().decode())
    except Exception as e:
        raise RuntimeError(f"Failed to get manifest for {feature_url}: {e}") from e

    # Get layer digest
    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(f"Failed to get layer digest for {feature_url}")
    digest = layers[0].get("digest", "")

    if not digest:
        raise RuntimeError(f"Failed to get layer digest for {feature_url}")

    # Download and extract layer
    blob_url = f"https://{registry}/v2/{path}/blobs/{digest}"
    req = urllib.request.Request(
        blob_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        import io
        import tarfile

        with urllib.request.urlopen(req) as response:
            # Response is a tar.gz - use tarfile's built-in gzip support
            data = response.read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(feature_dir)
    except Exception as e:
        raise RuntimeError(f"Failed to extract feature {feature_url}: {e}") from e


def clear_feature_cache() -> None:
    """Clear the feature cache directory."""
    shutil.rmtree(FEATURE_CACHE_DIR, ignore_errors=True)
