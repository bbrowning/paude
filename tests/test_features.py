"""Tests for dev container features."""

from __future__ import annotations

import json
from pathlib import Path

from paude.features.downloader import FEATURE_CACHE_DIR
from paude.features.installer import generate_feature_install_layer


class TestGenerateFeatureInstallLayer:
    """Tests for generate_feature_install_layer."""

    def test_creates_correct_copy_command(self, tmp_path: Path):
        """generate_feature_install_layer creates correct COPY command."""
        feature_dir = tmp_path / "abc123hash"
        feature_dir.mkdir()
        (feature_dir / "install.sh").write_text("#!/bin/bash\necho 'installing'")
        (feature_dir / "devcontainer-feature.json").write_text(
            json.dumps({"id": "test-feature"})
        )

        result = generate_feature_install_layer(feature_dir, {})
        # COPY uses relative path from build context: features/<hash>/
        assert "COPY features/abc123hash/ /tmp/features/test-feature/" in result

    def test_creates_correct_run_command(self, tmp_path: Path):
        """generate_feature_install_layer creates correct RUN command."""
        feature_dir = tmp_path / "test-feature"
        feature_dir.mkdir()
        (feature_dir / "install.sh").write_text("#!/bin/bash\necho 'installing'")
        (feature_dir / "devcontainer-feature.json").write_text(
            json.dumps({"id": "test-feature"})
        )

        result = generate_feature_install_layer(feature_dir, {})
        assert "RUN cd /tmp/features/test-feature && ./install.sh" in result

    def test_options_converted_to_uppercase_env_vars(self, tmp_path: Path):
        """Options are converted to uppercase env vars."""
        feature_dir = tmp_path / "test-feature"
        feature_dir.mkdir()
        (feature_dir / "install.sh").write_text("#!/bin/bash\necho 'installing'")
        (feature_dir / "devcontainer-feature.json").write_text(
            json.dumps({"id": "test-feature"})
        )

        result = generate_feature_install_layer(feature_dir, {"version": "3.11"})
        assert "VERSION=3.11" in result

    def test_multiple_options(self, tmp_path: Path):
        """Multiple options are all included."""
        feature_dir = tmp_path / "test-feature"
        feature_dir.mkdir()
        (feature_dir / "install.sh").write_text("#!/bin/bash")
        (feature_dir / "devcontainer-feature.json").write_text(
            json.dumps({"id": "python"})
        )

        result = generate_feature_install_layer(
            feature_dir, {"version": "3.11", "installTools": "true"}
        )
        assert "VERSION=3.11" in result
        assert "INSTALLTOOLS=true" in result


class TestFeatureCacheDir:
    """Tests for feature cache directory."""

    def test_cache_directory_path_is_correct(self):
        """Cache directory path follows XDG convention."""
        # Just verify the path structure
        assert "paude" in str(FEATURE_CACHE_DIR)
        assert "features" in str(FEATURE_CACHE_DIR)
