"""Tests for build_context module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from paude.config.models import PaudeConfig
from paude.container.build_context import (
    copy_entrypoints,
    generate_dockerfile_content,
    resolve_entrypoint,
)


class TestResolveEntrypoint:
    """Tests for resolve_entrypoint()."""

    def test_with_script_dir(self, tmp_path: Path) -> None:
        result = resolve_entrypoint(tmp_path)
        assert result == tmp_path / "containers" / "paude" / "entrypoint.sh"

    def test_without_script_dir(self) -> None:
        result = resolve_entrypoint(None)
        # Should return path under paude/container/data/ (bundled via force-include)
        assert result.name == "entrypoint.sh"
        assert "data" in str(result)

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

    def test_creates_fallback_session_entrypoint_when_missing(
        self, tmp_path: Path
    ) -> None:
        nonexistent = tmp_path / "nonexistent" / "entrypoint.sh"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        copy_entrypoints(nonexistent, dest_dir)

        session_dest = dest_dir / "entrypoint-session.sh"
        assert session_dest.exists()
        assert session_dest.read_text() == '#!/bin/bash\nexec "$@"\n'
        assert session_dest.stat().st_mode & 0o755 == 0o755

    def test_creates_fallback_tmux_conf_when_missing(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent" / "entrypoint.sh"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        copy_entrypoints(nonexistent, dest_dir)

        tmux_dest = dest_dir / "tmux.conf"
        assert tmux_dest.exists()
        assert tmux_dest.read_text() == "# auto-generated\n"

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

    def test_copies_entrypoint_library_files(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\n")
        for lib_name in [
            "entrypoint-lib-credentials.sh",
            "entrypoint-lib-config.sh",
            "entrypoint-lib-install.sh",
            "patch-proxy-fetch.sh",
        ]:
            (src_dir / lib_name).write_text(f"#!/bin/bash\r\n# {lib_name}\r\n")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        for lib_name in [
            "entrypoint-lib-credentials.sh",
            "entrypoint-lib-config.sh",
            "entrypoint-lib-install.sh",
            "patch-proxy-fetch.sh",
        ]:
            lib_dest = dest_dir / lib_name
            assert lib_dest.exists(), f"{lib_name} not copied"
            assert "\r\n" not in lib_dest.read_text()
            assert lib_dest.stat().st_mode & 0o755 == 0o755

    def test_skips_missing_library_files(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent" / "entrypoint.sh"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        copy_entrypoints(nonexistent, dest_dir)

        for lib_name in [
            "entrypoint-lib-credentials.sh",
            "entrypoint-lib-config.sh",
            "entrypoint-lib-install.sh",
            "patch-proxy-fetch.sh",
        ]:
            assert not (dest_dir / lib_name).exists()

    def test_skips_removed_otel_patch_files(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        entrypoint = src_dir / "entrypoint.sh"
        entrypoint.write_text("#!/bin/bash\n")
        for script_name in [
            "patch-gemini-otel-proxy.sh",
            "patch-openclaw-otel-proxy.sh",
            "patch-openclaw-otel-logs.sh",
        ]:
            (src_dir / script_name).write_text("#!/bin/bash\n")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        copy_entrypoints(entrypoint, dest_dir)

        for script_name in [
            "patch-gemini-otel-proxy.sh",
            "patch-openclaw-otel-proxy.sh",
            "patch-openclaw-otel-logs.sh",
        ]:
            assert not (dest_dir / script_name).exists()


class TestGenerateDockerfileContent:
    """Tests for generate_dockerfile_content()."""

    @patch("paude.config.dockerfile.generate_pip_install_dockerfile")
    def test_uses_pip_install_for_default_image(self, mock_pip: MagicMock) -> None:
        mock_pip.return_value = "FROM base\nRUN pip install"
        config = PaudeConfig()

        result = generate_dockerfile_content(config, using_default_paude_image=True)

        mock_pip.assert_called_once_with(
            config, include_claude_install=False, agent=None
        )
        assert result == "FROM base\nRUN pip install"

    @patch("paude.config.dockerfile.generate_workspace_dockerfile")
    def test_uses_workspace_for_custom_image(self, mock_ws: MagicMock) -> None:
        mock_ws.return_value = "FROM custom\nRUN setup"
        config = PaudeConfig()

        result = generate_dockerfile_content(config, using_default_paude_image=False)

        mock_ws.assert_called_once_with(config, agent=None)
        assert result == "FROM custom\nRUN setup"
