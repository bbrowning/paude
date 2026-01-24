"""Tests for configuration detection and parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paude.config import (
    ConfigError,
    PaudeConfig,
    detect_config,
    generate_workspace_dockerfile,
    parse_config,
)
from paude.config.claude_layer import generate_claude_layer_dockerfile
from paude.config.dockerfile import generate_pip_install_dockerfile


class TestDetectConfig:
    """Tests for config detection."""

    def test_finds_devcontainer_in_folder(self, tmp_path: Path):
        """detect_config finds .devcontainer/devcontainer.json."""
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        config_file = devcontainer_dir / "devcontainer.json"
        config_file.write_text('{"image": "python:3.11"}')

        result = detect_config(tmp_path)
        assert result == config_file

    def test_finds_devcontainer_in_root(self, tmp_path: Path):
        """detect_config finds .devcontainer.json."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text('{"image": "python:3.11"}')

        result = detect_config(tmp_path)
        assert result == config_file

    def test_finds_paude_json(self, tmp_path: Path):
        """detect_config finds paude.json."""
        config_file = tmp_path / "paude.json"
        config_file.write_text('{"base": "python:3.11"}')

        result = detect_config(tmp_path)
        assert result == config_file

    def test_respects_priority_order(self, tmp_path: Path):
        """detect_config respects priority order."""
        # Create all three config files
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        (devcontainer_dir / "devcontainer.json").write_text('{"image": "priority1"}')
        (tmp_path / ".devcontainer.json").write_text('{"image": "priority2"}')
        (tmp_path / "paude.json").write_text('{"base": "priority3"}')

        result = detect_config(tmp_path)
        assert result == devcontainer_dir / "devcontainer.json"

    def test_returns_none_when_no_config(self, tmp_path: Path):
        """detect_config returns None when no config exists."""
        result = detect_config(tmp_path)
        assert result is None


class TestParseConfig:
    """Tests for config parsing."""

    def test_parses_devcontainer_with_image(self, tmp_path: Path):
        """parse_config handles devcontainer with image."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text('{"image": "python:3.11-slim"}')

        config = parse_config(config_file)
        assert config.config_type == "devcontainer"
        assert config.base_image == "python:3.11-slim"
        assert config.dockerfile is None

    def test_parses_devcontainer_with_dockerfile(self, tmp_path: Path):
        """parse_config handles devcontainer with dockerfile and context."""
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        config_file = devcontainer_dir / "devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "build": {
                        "dockerfile": "Dockerfile",
                        "context": "..",
                    }
                }
            )
        )

        config = parse_config(config_file)
        assert config.config_type == "devcontainer"
        assert config.dockerfile == devcontainer_dir / "Dockerfile"
        # Context should resolve to tmp_path (the parent of .devcontainer)
        assert config.build_context == tmp_path

    def test_resolves_relative_dockerfile_paths(self, tmp_path: Path):
        """parse_config resolves relative dockerfile paths correctly."""
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        config_file = devcontainer_dir / "devcontainer.json"
        config_file.write_text('{"build": {"dockerfile": "../custom/Dockerfile"}}')

        config = parse_config(config_file)
        expected = devcontainer_dir / ".." / "custom" / "Dockerfile"
        assert config.dockerfile == expected

    def test_parses_paude_json_with_packages(self, tmp_path: Path):
        """parse_config handles paude.json with packages."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps({"base": "node:22-slim", "packages": ["git", "make", "gcc"]})
        )

        config = parse_config(config_file)
        assert config.config_type == "paude"
        assert config.base_image == "node:22-slim"
        assert config.packages == ["git", "make", "gcc"]

    def test_parses_paude_json_with_setup(self, tmp_path: Path):
        """parse_config handles paude.json with setup command."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps({"base": "python:3.11", "setup": "pip install -r requirements.txt"})
        )

        config = parse_config(config_file)
        assert config.post_create_command == "pip install -r requirements.txt"

    def test_handles_invalid_json(self, tmp_path: Path):
        """parse_config handles invalid JSON."""
        config_file = tmp_path / "paude.json"
        config_file.write_text("{ invalid json }")

        with pytest.raises(ConfigError):
            parse_config(config_file)

    def test_parses_venv_auto(self, tmp_path: Path):
        """parse_config handles venv: auto."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"venv": "auto"}))

        config = parse_config(config_file)
        assert config.venv == "auto"

    def test_parses_venv_none(self, tmp_path: Path):
        """parse_config handles venv: none."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"venv": "none"}))

        config = parse_config(config_file)
        assert config.venv == "none"

    def test_parses_venv_list(self, tmp_path: Path):
        """parse_config handles venv as list of directories."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"venv": [".venv", "my-custom-venv"]}))

        config = parse_config(config_file)
        assert config.venv == [".venv", "my-custom-venv"]

    def test_venv_defaults_to_auto(self, tmp_path: Path):
        """parse_config defaults venv to auto when not specified."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"base": "python:3.11"}))

        config = parse_config(config_file)
        assert config.venv == "auto"

    def test_venv_invalid_value_raises_error(self, tmp_path: Path):
        """parse_config raises error for invalid venv value."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"venv": "invalid"}))

        with pytest.raises(ConfigError, match="Invalid venv config"):
            parse_config(config_file)

    def test_venv_list_with_non_string_raises_error(self, tmp_path: Path):
        """parse_config raises error for venv list with non-string item."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"venv": [".venv", 123]}))

        with pytest.raises(ConfigError, match="list items must be strings"):
            parse_config(config_file)

    def test_parses_pip_install_true(self, tmp_path: Path):
        """parse_config handles pip_install: true."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"pip_install": True}))

        config = parse_config(config_file)
        assert config.pip_install is True

    def test_parses_pip_install_false(self, tmp_path: Path):
        """parse_config handles pip_install: false."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"pip_install": False}))

        config = parse_config(config_file)
        assert config.pip_install is False

    def test_parses_pip_install_custom_command(self, tmp_path: Path):
        """parse_config handles pip_install as custom command string."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"pip_install": "pip install -r requirements.txt"}))

        config = parse_config(config_file)
        assert config.pip_install == "pip install -r requirements.txt"

    def test_pip_install_defaults_to_false(self, tmp_path: Path):
        """parse_config defaults pip_install to False when not specified."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"base": "python:3.11"}))

        config = parse_config(config_file)
        assert config.pip_install is False

    def test_pip_install_invalid_value_raises_error(self, tmp_path: Path):
        """parse_config raises error for invalid pip_install value."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"pip_install": ["invalid"]}))

        with pytest.raises(ConfigError, match="Invalid pip_install config"):
            parse_config(config_file)

    def test_warns_unsupported_properties(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """parse_config logs warnings for unsupported properties."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "image": "python:3.11",
                    "mounts": ["/host:/container"],
                    "runArgs": ["--privileged"],
                }
            )
        )

        parse_config(config_file)
        captured = capsys.readouterr()
        assert "mounts" in captured.err
        assert "runArgs" in captured.err

    def test_parses_post_create_command_array(self, tmp_path: Path):
        """parse_config handles postCreateCommand as array."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps({"image": "python:3.11", "postCreateCommand": ["npm", "install"]})
        )

        config = parse_config(config_file)
        assert config.post_create_command == "npm && install"


class TestGenerateWorkspaceDockerfile:
    """Tests for Dockerfile generation."""

    def test_generates_basic_dockerfile(self):
        """generate_workspace_dockerfile produces valid output."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
        assert "USER paude" in dockerfile

    def test_includes_packages_when_present(self):
        """generate_workspace_dockerfile includes packages when present."""
        config = PaudeConfig(packages=["vim", "tmux"])
        dockerfile = generate_workspace_dockerfile(config)

        assert "vim tmux" in dockerfile
        assert "User-specified packages from paude.json" in dockerfile

    def test_handles_image_based_config(self):
        """generate_workspace_dockerfile handles image-based configs."""
        config = PaudeConfig(
            config_type="devcontainer",
            base_image="python:3.11-slim",
        )
        dockerfile = generate_workspace_dockerfile(config)

        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "ENTRYPOINT" in dockerfile

    def test_pip_install_true_generates_editable_install(self):
        """generate_workspace_dockerfile with pip_install=True uses editable install."""
        config = PaudeConfig(pip_install=True)
        dockerfile = generate_workspace_dockerfile(config)

        assert "COPY . /opt/workspace-src" in dockerfile
        assert "RUN python3 -m venv /opt/venv" in dockerfile
        assert "/opt/venv/bin/pip install -e /opt/workspace-src" in dockerfile
        assert "RUN chown -R paude:0 /opt/venv" in dockerfile

    def test_pip_install_custom_command(self):
        """generate_workspace_dockerfile with pip_install string uses custom command."""
        config = PaudeConfig(pip_install="pip install -r requirements.txt")
        dockerfile = generate_workspace_dockerfile(config)

        assert "COPY . /opt/workspace-src" in dockerfile
        assert "RUN python3 -m venv /opt/venv" in dockerfile
        assert "/opt/venv/bin/pip install -r requirements.txt" in dockerfile

    def test_pip_install_false_no_venv_setup(self):
        """generate_workspace_dockerfile with pip_install=False has no venv setup."""
        config = PaudeConfig(pip_install=False)
        dockerfile = generate_workspace_dockerfile(config)

        assert "/opt/workspace-src" not in dockerfile
        assert "/opt/venv" not in dockerfile


class TestGeneratePipInstallDockerfile:
    """Tests for pip_install Dockerfile generation."""

    def test_generates_minimal_dockerfile(self):
        """generate_pip_install_dockerfile produces minimal output."""
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(config)

        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        # Should NOT include Claude installation by default
        assert "claude.ai/install.sh" not in dockerfile

    def test_include_claude_install(self):
        """generate_pip_install_dockerfile includes Claude when requested."""
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        assert "curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
        assert "DISABLE_AUTOUPDATER=1" in dockerfile
        assert "/home/paude/.local/bin" in dockerfile
        assert "chmod -R g+rwX /home/paude" in dockerfile

    def test_include_claude_install_with_pip_install(self):
        """generate_pip_install_dockerfile includes both Claude and pip_install."""
        config = PaudeConfig(pip_install=True)
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        assert "claude.ai/install.sh" in dockerfile
        assert "/opt/venv/bin/pip install -e /opt/workspace-src" in dockerfile

    def test_claude_install_before_pip_install(self):
        """Claude installation comes before pip_install in generated Dockerfile."""
        config = PaudeConfig(pip_install=True)
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        # Find positions of key instructions
        claude_pos = dockerfile.find("claude.ai/install.sh")
        pip_pos = dockerfile.find("/opt/venv/bin/pip")

        assert claude_pos != -1, "Claude install not found"
        assert pip_pos != -1, "pip install not found"
        assert claude_pos < pip_pos, "Claude should be installed before pip_install"

    def test_multiple_user_paude_lines_with_pip_install(self):
        """Dockerfile with pip_install has correct USER paude sequence."""
        config = PaudeConfig(pip_install=True)
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        # Should have multiple USER paude lines (one for claude install, one at end)
        user_paude_count = dockerfile.count("USER paude")
        assert user_paude_count >= 2, f"Expected at least 2 'USER paude' lines, got {user_paude_count}"

    def test_ends_with_user_paude_when_claude_only(self):
        """Dockerfile with only Claude install ends with USER paude, not root."""
        config = PaudeConfig(pip_install=False)
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        lines = dockerfile.strip().split("\n")
        # Find the last USER directive
        last_user_line = None
        for line in reversed(lines):
            if line.strip().startswith("USER"):
                last_user_line = line.strip()
                break

        assert last_user_line == "USER paude", f"Expected 'USER paude', got '{last_user_line}'"

    def test_starts_with_user_root_for_feature_injection(self):
        """Dockerfile with include_claude_install has USER root before USER paude.

        This ensures features (injected before first USER paude) run as root,
        even when the base image ends with a non-root user.
        """
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=True)

        # Find positions of USER directives
        lines = dockerfile.split("\n")
        user_lines = [(i, line.strip()) for i, line in enumerate(lines)
                      if line.strip().startswith("USER")]

        assert len(user_lines) >= 2, "Expected at least 2 USER lines"
        # First USER should be root
        assert user_lines[0][1] == "USER root", f"First USER should be 'USER root', got '{user_lines[0][1]}'"
        # Second USER should be paude
        assert user_lines[1][1] == "USER paude", f"Second USER should be 'USER paude', got '{user_lines[1][1]}'"

    def test_minimal_dockerfile_has_user_paude_for_features(self):
        """Minimal Dockerfile (no claude, no pip_install) still has USER paude for features.

        This ensures features can be injected even when using the default paude image
        without pip_install. Features are injected before the first USER paude line.
        """
        config = PaudeConfig(pip_install=False)
        dockerfile = generate_pip_install_dockerfile(config, include_claude_install=False)

        # Should have USER paude even with minimal config
        assert "USER paude" in dockerfile, "Minimal Dockerfile should have USER paude for feature injection"
        # Should have USER root before USER paude for features to run as root
        lines = dockerfile.split("\n")
        user_lines = [(i, line.strip()) for i, line in enumerate(lines)
                      if line.strip().startswith("USER")]

        assert len(user_lines) >= 2, "Expected at least 2 USER lines"
        assert user_lines[0][1] == "USER root", "First USER should be root for feature injection"
        assert user_lines[1][1] == "USER paude", "Second USER should be paude"


class TestGenerateClaudeLayerDockerfile:
    """Tests for Claude layer Dockerfile generation."""

    def test_generates_claude_layer(self):
        """generate_claude_layer_dockerfile produces expected output."""
        dockerfile = generate_claude_layer_dockerfile()

        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
        assert "DISABLE_AUTOUPDATER=1" in dockerfile
        assert "/home/paude/.local/bin" in dockerfile
        assert "chmod -R g+rwX /home/paude" in dockerfile
        assert 'ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]' in dockerfile

    def test_claude_layer_runs_as_paude_user(self):
        """generate_claude_layer_dockerfile installs as paude user."""
        dockerfile = generate_claude_layer_dockerfile()

        # Find the Claude install line and verify it's preceded by USER paude
        lines = dockerfile.split("\n")
        for i, line in enumerate(lines):
            if "claude.ai/install.sh" in line:
                # Look backwards for USER paude
                for j in range(i - 1, -1, -1):
                    if lines[j].strip().startswith("USER"):
                        assert "paude" in lines[j]
                        break
                break
