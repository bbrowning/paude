"""Tests for build_context module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from paude.config.models import FeatureSpec, PaudeConfig
from paude.container.build_context import (
    BuildContext,
    _add_stage_alias,
    _write_dockerignore,
    copy_entrypoints,
    copy_features_cache,
    generate_dockerfile_content,
    inject_features,
    prepare_build_context,
    resolve_entrypoint,
)


class TestResolveEntrypoint:
    """Tests for resolve_entrypoint()."""

    def test_with_script_dir(self, tmp_path: Path) -> None:
        result = resolve_entrypoint(tmp_path)
        assert result == tmp_path / "containers" / "paude" / "entrypoint.sh"

    def test_without_script_dir(self) -> None:
        result = resolve_entrypoint(None)
        # Should return path relative to package root
        assert result.name == "entrypoint.sh"
        assert "containers" in str(result)

    def test_paths_are_path_objects(self, tmp_path: Path) -> None:
        assert isinstance(resolve_entrypoint(tmp_path), Path)
        assert isinstance(resolve_entrypoint(None), Path)


class TestCopyEntrypoints:
    """Tests for copy_entrypoints()."""

    def test_copies_entrypoint_with_unix_line_endings(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\r\necho hello\r\n")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        result = (dest_dir / "entrypoint.sh").read_text()
        assert "\r\n" not in result
        assert result == "#!/bin/bash\necho hello\n"

    def test_creates_fallback_entrypoint_when_missing(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent" / "entrypoint.sh"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        copy_entrypoints(nonexistent, dest_dir)

        result = (dest_dir / "entrypoint.sh").read_text()
        assert result == '#!/bin/bash\nexec claude "$@"\n'

    def test_entrypoint_is_executable(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\n")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        mode = (dest_dir / "entrypoint.sh").stat().st_mode
        assert mode & 0o755 == 0o755

    def test_copies_session_entrypoint_when_exists(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\n")
        session = src_dir / "entrypoint-session.sh"
        session.write_text("#!/bin/bash\r\nsession\r\n")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        session_dest = dest_dir / "entrypoint-session.sh"
        assert session_dest.exists()
        assert "\r\n" not in session_dest.read_text()
        assert session_dest.stat().st_mode & 0o755 == 0o755

    def test_copies_tmux_conf_when_exists(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\n")
        tmux_conf = src_dir / "tmux.conf"
        tmux_conf.write_text('set-option -g default-terminal "tmux-256color"\n')

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        assert (dest_dir / "tmux.conf").exists()
        assert "tmux-256color" in (dest_dir / "tmux.conf").read_text()


class TestInjectFeatures:
    """Tests for inject_features()."""

    def test_returns_unchanged_when_no_features(self) -> None:
        content = "FROM ubuntu\nUSER paude\nRUN echo hi"
        assert inject_features(content, None) == content
        assert inject_features(content, []) == content

    @patch("paude.features.installer.generate_features_dockerfile")
    def test_injects_before_user_paude(self, mock_gen: MagicMock) -> None:
        mock_gen.return_value = "\n# FEATURES\nRUN install-feature\n"
        features = [FeatureSpec(url="ghcr.io/test/feature:1")]
        content = "FROM ubuntu\nUSER paude\nRUN echo hi"

        result = inject_features(content, features)

        assert "\n# FEATURES\nRUN install-feature\n\nUSER paude" in result
        mock_gen.assert_called_once_with(features)

    @patch("paude.features.installer.generate_features_dockerfile")
    def test_only_replaces_first_user_paude(self, mock_gen: MagicMock) -> None:
        mock_gen.return_value = "\n# FEAT\n"
        features = [FeatureSpec(url="ghcr.io/test/feature:1")]
        content = "FROM ubuntu\nUSER paude\nRUN something\nUSER paude"

        result = inject_features(content, features)

        # The features block should appear only once
        assert result.count("# FEAT") == 1
        # Second USER paude should remain untouched
        assert result.endswith("USER paude")


class TestCopyFeaturesCache:
    """Tests for copy_features_cache()."""

    @patch("paude.container.build_context.shutil.copytree")
    @patch("paude.features.downloader.FEATURE_CACHE_DIR")
    def test_copies_cache_when_exists(
        self, mock_cache_dir: MagicMock, mock_copytree: MagicMock, tmp_path: Path
    ) -> None:
        mock_cache_dir.exists.return_value = True
        copy_features_cache(tmp_path)
        mock_copytree.assert_called_once_with(mock_cache_dir, tmp_path / "features")

    @patch("paude.container.build_context.shutil.copytree")
    @patch("paude.features.downloader.FEATURE_CACHE_DIR")
    def test_skips_when_no_cache(
        self, mock_cache_dir: MagicMock, mock_copytree: MagicMock, tmp_path: Path
    ) -> None:
        mock_cache_dir.exists.return_value = False
        copy_features_cache(tmp_path)
        mock_copytree.assert_not_called()


class TestGenerateDockerfileContent:
    """Tests for generate_dockerfile_content()."""

    @patch("paude.container.build_context.inject_features")
    @patch("paude.config.dockerfile.generate_pip_install_dockerfile")
    def test_uses_pip_install_for_default_image(
        self, mock_pip: MagicMock, mock_inject: MagicMock
    ) -> None:
        mock_pip.return_value = "FROM base\nRUN pip install"
        mock_inject.return_value = "FROM base\nRUN pip install"
        config = PaudeConfig()

        generate_dockerfile_content(config, using_default_paude_image=True)

        mock_pip.assert_called_once_with(
            config, include_claude_install=False, agent=None
        )
        mock_inject.assert_called_once()

    @patch("paude.container.build_context.inject_features")
    @patch("paude.config.dockerfile.generate_workspace_dockerfile")
    def test_uses_workspace_for_custom_image(
        self, mock_ws: MagicMock, mock_inject: MagicMock
    ) -> None:
        mock_ws.return_value = "FROM custom\nRUN setup"
        mock_inject.return_value = "FROM custom\nRUN setup"
        config = PaudeConfig()

        generate_dockerfile_content(config, using_default_paude_image=False)

        mock_ws.assert_called_once_with(config, agent=None)
        mock_inject.assert_called_once()

    @patch("paude.container.build_context.inject_features")
    @patch("paude.config.dockerfile.generate_pip_install_dockerfile")
    def test_injects_features(
        self, mock_pip: MagicMock, mock_inject: MagicMock
    ) -> None:
        mock_pip.return_value = "FROM base"
        mock_inject.return_value = "FROM base"
        features = [FeatureSpec(url="ghcr.io/test/feat:1")]
        config = PaudeConfig(features=features)

        generate_dockerfile_content(config, using_default_paude_image=True)

        mock_inject.assert_called_once_with("FROM base", features)


class TestWriteDockerignore:
    """Tests for _write_dockerignore()."""

    def test_writes_dockerignore_file(self, tmp_path: Path) -> None:
        _write_dockerignore(tmp_path)

        ignore_file = tmp_path / ".dockerignore"
        assert ignore_file.exists()
        content = ignore_file.read_text()
        assert ".venv" in content
        assert "__pycache__" in content
        assert ".git" in content
        assert "node_modules" in content


class TestAddStageAlias:
    """Tests for _add_stage_alias()."""

    def test_adds_alias_to_from_line(self) -> None:
        result = _add_stage_alias("FROM ubuntu:22.04")
        assert result == "FROM ubuntu:22.04 AS user-base"

    def test_preserves_existing_alias(self) -> None:
        result = _add_stage_alias("FROM ubuntu:22.04 AS mybase")
        assert result == "FROM ubuntu:22.04 AS mybase"

    def test_handles_multiline_dockerfile(self) -> None:
        dockerfile = "FROM ubuntu:22.04\nRUN apt-get update\nFROM python:3.11"
        result = _add_stage_alias(dockerfile)
        lines = result.split("\n")
        assert lines[0] == "FROM ubuntu:22.04 AS user-base"
        # Second FROM should be untouched
        assert lines[2] == "FROM python:3.11"


class TestPrepareBuildContext:
    """Integration-style tests for prepare_build_context()."""

    @patch("paude.container.build_context._resolve_default_base")
    @patch("paude.container.build_context.generate_dockerfile_content")
    @patch("paude.container.build_context.compute_config_hash", return_value="abc123")
    def test_returns_build_context_with_default_image(
        self,
        mock_hash: MagicMock,
        mock_gen: MagicMock,
        mock_resolve: MagicMock,
    ) -> None:
        mock_resolve.return_value = "quay.io/bbrowning/paude-base-centos9:1.0"
        mock_gen.return_value = "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nRUN echo hi"
        config = PaudeConfig()

        import shutil

        try:
            result = prepare_build_context(config)
            assert isinstance(result, BuildContext)
            assert result.config_hash == "abc123"
            assert result.base_image == "quay.io/bbrowning/paude-base-centos9:1.0"
            assert result.dockerfile_path.exists()
            # Verify BASE_IMAGE was replaced
            content = result.dockerfile_path.read_text()
            assert "ARG BASE_IMAGE" not in content
            assert "quay.io/bbrowning/paude-base-centos9:1.0" in content
        finally:
            if result.context_dir.exists():
                shutil.rmtree(result.context_dir)

    @patch("paude.container.build_context.compute_config_hash", return_value="abc123")
    def test_raises_on_missing_dockerfile(
        self, mock_hash: MagicMock, tmp_path: Path
    ) -> None:
        missing = tmp_path / "Dockerfile"
        config = PaudeConfig(dockerfile=missing)

        with pytest.raises(FileNotFoundError, match="Dockerfile not found"):
            prepare_build_context(config)
