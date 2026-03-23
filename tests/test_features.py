"""Tests for dev container features."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

from paude.features.downloader import FEATURE_CACHE_DIR, _download_with_curl
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


def _make_tar_bytes(compress: bool = False) -> bytes:
    """Create a tar archive containing install.sh, optionally gzip-compressed."""
    buf = io.BytesIO()
    mode = "w:gz" if compress else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        content = b"#!/bin/bash\necho hello"
        info = tarfile.TarInfo(name="install.sh")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _mock_urlopen_responses(*data_sequence: bytes):
    """Create a side_effect for urlopen that returns responses in order."""
    responses = [io.BytesIO(d) for d in data_sequence]
    it = iter(responses)
    return lambda *_args, **_kwargs: next(it)


class TestDownloadWithCurl:
    """Tests for _download_with_curl extraction."""

    def _run(self, tmp_path: Path, compress: bool) -> None:
        feature_dir = tmp_path / "feature"
        feature_dir.mkdir()

        token = json.dumps({"token": "fake"}).encode()
        manifest = json.dumps(
            {
                "layers": [{"digest": "sha256:abc123"}],
            }
        ).encode()
        blob = _make_tar_bytes(compress=compress)

        with patch(
            "urllib.request.urlopen",
            side_effect=_mock_urlopen_responses(token, manifest, blob),
        ):
            _download_with_curl("ghcr.io/devcontainers/features/node:1", feature_dir)

        assert (feature_dir / "install.sh").exists()
        assert (feature_dir / "install.sh").read_text().startswith("#!/bin/bash")

    def test_extracts_uncompressed_tar(self, tmp_path: Path):
        """Uncompressed tar layer blobs are extracted correctly."""
        self._run(tmp_path, compress=False)

    def test_extracts_gzip_tar(self, tmp_path: Path):
        """Gzip-compressed tar layer blobs are extracted correctly."""
        self._run(tmp_path, compress=True)


class TestFeatureCacheDir:
    """Tests for feature cache directory."""

    def test_cache_directory_path_is_correct(self):
        """Cache directory path follows XDG convention."""
        # Just verify the path structure
        assert "paude" in str(FEATURE_CACHE_DIR)
        assert "features" in str(FEATURE_CACHE_DIR)
