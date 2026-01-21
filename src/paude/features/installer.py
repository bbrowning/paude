"""Feature installation for dev container features."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paude.config.models import FeatureSpec


def generate_feature_install_layer(feature_path: Path, options: dict[str, Any]) -> str:
    """Generate Dockerfile RUN instruction for a feature.

    Args:
        feature_path: Path to extracted feature directory.
        options: Feature options dictionary.

    Returns:
        Dockerfile snippet for installing this feature.
    """
    lines: list[str] = []

    # Read feature metadata
    feature_json = feature_path / "devcontainer-feature.json"
    if feature_json.exists():
        try:
            metadata = json.loads(feature_json.read_text())
            feature_id = metadata.get("id", "unknown")
        except json.JSONDecodeError:
            feature_id = "unknown"
    else:
        feature_id = feature_path.name

    lines.append("")
    lines.append(f"# Feature: {feature_id}")

    # Convert options to environment variables (uppercase keys)
    env_vars = ""
    if options:
        env_parts = [f"{k.upper()}={v}" for k, v in options.items()]
        env_vars = " ".join(env_parts) + " "

    # Generate COPY and RUN
    # Use relative path from build context (features/<hash>/)
    # The feature cache is copied to build context as "features/"
    feature_hash = feature_path.name  # The hash directory name
    lines.append(f"COPY features/{feature_hash}/ /tmp/features/{feature_id}/")
    if env_vars:
        lines.append(f"RUN cd /tmp/features/{feature_id} && {env_vars}./install.sh")
    else:
        lines.append(f"RUN cd /tmp/features/{feature_id} && ./install.sh")

    return "\n".join(lines)


def generate_features_dockerfile(features: list[FeatureSpec]) -> str:
    """Generate complete Dockerfile section for all features.

    Args:
        features: List of feature specifications.

    Returns:
        Dockerfile content for installing all features.
    """
    if not features:
        return ""

    from paude.features.downloader import download_feature

    lines: list[str] = []
    lines.append("")
    lines.append("# === Dev Container Features ===")

    for feature in features:
        feature_dir = download_feature(feature.url)
        layer = generate_feature_install_layer(feature_dir, feature.options)
        lines.append(layer)

    lines.append("")
    lines.append("# Cleanup feature installers")
    lines.append("RUN rm -rf /tmp/features")

    return "\n".join(lines)
