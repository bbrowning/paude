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
            json.dumps(
                {"base": "python:3.11", "setup": "pip install -r requirements.txt"}
            )
        )

        config = parse_config(config_file)
        assert config.post_create_command == "pip install -r requirements.txt"

    def test_handles_invalid_json(self, tmp_path: Path):
        """parse_config handles invalid JSON."""
        config_file = tmp_path / "paude.json"
        config_file.write_text("{ invalid json }")

        with pytest.raises(ConfigError):
            parse_config(config_file)

    def test_pip_install_deprecated_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """parse_config warns about deprecated pip_install."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"pip_install": True}))

        parse_config(config_file)
        captured = capsys.readouterr()
        assert "pip_install" in captured.err
        assert "deprecated" in captured.err

    def test_warns_unsupported_properties(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
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
            json.dumps(
                {"image": "python:3.11", "postCreateCommand": ["npm", "install"]}
            )
        )

        config = parse_config(config_file)
        assert config.post_create_command == "npm && install"

    def test_parses_devcontainer_features(self, tmp_path: Path):
        """parse_config extracts features with options from devcontainer.json."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "image": "python:3.11",
                    "features": {
                        "ghcr.io/devcontainers/features/node:1": {"version": "20"},
                        "ghcr.io/devcontainers/features/go:1": {},
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert len(config.features) == 2
        urls = {f.url for f in config.features}
        assert "ghcr.io/devcontainers/features/node:1" in urls
        assert "ghcr.io/devcontainers/features/go:1" in urls
        node_feature = next(f for f in config.features if "node" in f.url)
        assert node_feature.options == {"version": "20"}

    def test_parses_devcontainer_container_env(self, tmp_path: Path):
        """parse_config extracts containerEnv from devcontainer.json."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "image": "python:3.11",
                    "containerEnv": {"FOO": "bar", "BAZ": "qux"},
                }
            )
        )

        config = parse_config(config_file)
        assert config.container_env == {"FOO": "bar", "BAZ": "qux"}

    def test_parses_post_create_command_string(self, tmp_path: Path):
        """parse_config handles postCreateCommand as a plain string."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "image": "python:3.11",
                    "postCreateCommand": "make setup && make install",
                }
            )
        )

        config = parse_config(config_file)
        assert config.post_create_command == "make setup && make install"

    def test_parses_build_args(self, tmp_path: Path):
        """parse_config extracts build args from devcontainer.json."""
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        config_file = devcontainer_dir / "devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "build": {
                        "dockerfile": "Dockerfile",
                        "args": {"NODE_VERSION": "20", "DEBUG": "true"},
                    }
                }
            )
        )

        config = parse_config(config_file)
        assert config.build_args == {"NODE_VERSION": "20", "DEBUG": "true"}

    def test_handles_absolute_dockerfile_path(self, tmp_path: Path):
        """parse_config handles absolute dockerfile path without resolving relative."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps({"build": {"dockerfile": "/opt/docker/Dockerfile"}})
        )

        config = parse_config(config_file)
        assert config.dockerfile == Path("/opt/docker/Dockerfile")

    def test_unknown_config_type_raises(self, tmp_path: Path):
        """parse_config raises ConfigError for unknown file type."""
        config_file = tmp_path / "unknown.yaml"
        config_file.write_text('{"image": "python:3.11"}')

        with pytest.raises(ConfigError, match="Unknown config file type"):
            parse_config(config_file)

    def test_handles_unreadable_file(self, tmp_path: Path):
        """parse_config raises ConfigError for missing file."""
        config_file = tmp_path / "paude.json"
        # File doesn't exist

        with pytest.raises(ConfigError, match="Cannot read"):
            parse_config(config_file)

    def test_parses_paude_json_with_build_config(self, tmp_path: Path):
        """parse_config handles paude.json with dockerfile and build args."""
        config_file = tmp_path / "paude.json"
        dockerfile = tmp_path / "Dockerfile.custom"
        dockerfile.write_text("FROM python:3.11")
        config_file.write_text(
            json.dumps(
                {
                    "base": "python:3.11",
                    "build": {
                        "dockerfile": "Dockerfile.custom",
                        "args": {"PY_VER": "3.11"},
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert config.dockerfile == tmp_path / "Dockerfile.custom"
        assert config.build_args == {"PY_VER": "3.11"}
        assert config.build_context == tmp_path

    def test_parses_minimal_paude_json(self, tmp_path: Path):
        """parse_config handles empty paude.json with defaults."""
        config_file = tmp_path / "paude.json"
        config_file.write_text("{}")

        config = parse_config(config_file)
        assert config.config_type == "paude"
        assert config.base_image is None
        assert config.dockerfile is None
        assert config.packages == []
        assert config.post_create_command is None
        assert config.build_args == {}

    def test_parses_paude_json_create_section(self, tmp_path: Path):
        """parse_config extracts create section from paude.json."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps(
                {
                    "base": "python:3.11",
                    "create": {
                        "allowed-domains": [".vllm.ai", ".openai.com"],
                        "agent": "gemini",
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert config.create_allowed_domains == [".vllm.ai", ".openai.com"]
        assert config.create_agent == "gemini"

    def test_parses_paude_json_create_section_empty(self, tmp_path: Path):
        """parse_config handles missing create section gracefully."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"base": "python:3.11"}))

        config = parse_config(config_file)
        assert config.create_allowed_domains == []
        assert config.create_agent is None

    def test_parses_devcontainer_create_section(self, tmp_path: Path):
        """parse_config extracts create hints from devcontainer customizations."""
        config_file = tmp_path / ".devcontainer.json"
        config_file.write_text(
            json.dumps(
                {
                    "image": "python:3.11",
                    "customizations": {
                        "paude": {
                            "create": {
                                "allowed-domains": ["golang", ".example.com"],
                                "agent": "claude",
                            }
                        }
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert config.create_allowed_domains == ["golang", ".example.com"]
        assert config.create_agent == "claude"

    def test_warns_unknown_create_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """parse_config warns about unknown keys in create section."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"create": {"unknown-field": "value"}}))

        parse_config(config_file)
        captured = capsys.readouterr()
        assert "Unknown key 'unknown-field'" in captured.err


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

    def test_no_workspace_copy_in_dockerfile(self):
        """generate_workspace_dockerfile does not copy workspace source."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "/opt/workspace-src" not in dockerfile

    def test_includes_essential_utilities(self):
        """generate_workspace_dockerfile includes essential CLI utilities."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        for pkg in [
            "findutils",
            "grep",
            "sed",
            "gawk",
            "diffutils",
            "less",
            "file",
            "unzip",
            "zip",
        ]:
            assert pkg in dockerfile, f"Expected package '{pkg}' in Dockerfile"

    def test_dnf_uses_allowerasing(self):
        """generate_workspace_dockerfile uses --allowerasing for dnf to replace coreutils-single."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "--allowerasing" in dockerfile

    def test_copies_tmux_conf(self):
        """generate_workspace_dockerfile includes COPY for tmux.conf."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "COPY --chmod=664 tmux.conf" in dockerfile
        assert ".tmux.conf" in dockerfile


class TestGeneratePipInstallDockerfile:
    """Tests for feature layer Dockerfile generation."""

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
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=True
        )

        assert (
            "umask 0002 && curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
        )
        assert "DISABLE_AUTOUPDATER=1" in dockerfile
        assert "/home/paude/.local/bin" in dockerfile

    def test_ends_with_user_paude_when_claude_only(self):
        """Dockerfile with only Claude install ends with USER paude, not root."""
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=True
        )

        lines = dockerfile.strip().split("\n")
        # Find the last USER directive
        last_user_line = None
        for line in reversed(lines):
            if line.strip().startswith("USER"):
                last_user_line = line.strip()
                break

        assert last_user_line == "USER paude", (
            f"Expected 'USER paude', got '{last_user_line}'"
        )

    def test_starts_with_user_root_for_feature_injection(self):
        """Dockerfile with include_claude_install has USER root before USER paude.

        This ensures features (injected before first USER paude) run as root,
        even when the base image ends with a non-root user.
        """
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=True
        )

        # Find positions of USER directives
        lines = dockerfile.split("\n")
        user_lines = [
            (i, line.strip())
            for i, line in enumerate(lines)
            if line.strip().startswith("USER")
        ]

        assert len(user_lines) >= 2, "Expected at least 2 USER lines"
        # First USER should be root
        assert user_lines[0][1] == "USER root", (
            f"First USER should be 'USER root', got '{user_lines[0][1]}'"
        )
        # Second USER should be paude
        assert user_lines[1][1] == "USER paude", (
            f"Second USER should be 'USER paude', got '{user_lines[1][1]}'"
        )

    def test_minimal_dockerfile_has_user_paude_for_features(self):
        """Minimal Dockerfile (no claude) still has USER paude for features.

        This ensures features can be injected even when using the default paude image.
        Features are injected before the first USER paude line.
        """
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=False
        )

        # Should have USER paude even with minimal config
        assert "USER paude" in dockerfile, (
            "Minimal Dockerfile should have USER paude for feature injection"
        )
        # Should have USER root before USER paude for features to run as root
        lines = dockerfile.split("\n")
        user_lines = [
            (i, line.strip())
            for i, line in enumerate(lines)
            if line.strip().startswith("USER")
        ]

        assert len(user_lines) >= 2, "Expected at least 2 USER lines"
        assert user_lines[0][1] == "USER root", (
            "First USER should be root for feature injection"
        )
        assert user_lines[1][1] == "USER paude", "Second USER should be paude"


class TestGenerateClaudeLayerDockerfile:
    """Tests for Claude layer Dockerfile generation."""

    def test_generates_claude_layer(self):
        """generate_claude_layer_dockerfile produces expected output."""
        dockerfile = generate_claude_layer_dockerfile()

        assert "ARG BASE_IMAGE" in dockerfile
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert (
            "umask 0002 && curl -fsSL https://claude.ai/install.sh | bash" in dockerfile
        )
        assert "DISABLE_AUTOUPDATER=1" in dockerfile
        assert "/home/paude/.local/bin" in dockerfile
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
