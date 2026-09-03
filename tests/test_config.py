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

    def test_ignores_devcontainer_files(self, tmp_path: Path):
        """detect_config ignores both legacy devcontainer filename forms."""
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        (devcontainer_dir / "devcontainer.json").write_text('{"image": "python:3.11"}')
        (tmp_path / ".devcontainer.json").write_text('{"image": "node:22"}')

        assert detect_config(tmp_path) is None

    def test_finds_paude_json(self, tmp_path: Path):
        """detect_config finds paude.json."""
        config_file = tmp_path / "paude.json"
        config_file.write_text('{"base": "python:3.11"}')

        result = detect_config(tmp_path)
        assert result == config_file

    def test_respects_priority_order(self, tmp_path: Path):
        """detect_config respects priority order."""
        # A paude.json is selected even when legacy files are present.
        devcontainer_dir = tmp_path / ".devcontainer"
        devcontainer_dir.mkdir()
        (devcontainer_dir / "devcontainer.json").write_text('{"image": "priority1"}')
        (tmp_path / ".devcontainer.json").write_text('{"image": "priority2"}')
        (tmp_path / "paude.json").write_text('{"base": "priority3"}')

        result = detect_config(tmp_path)
        assert result == tmp_path / "paude.json"

    def test_returns_none_when_no_config(self, tmp_path: Path):
        """detect_config returns None when no config exists."""
        result = detect_config(tmp_path)
        assert result is None


class TestParseConfig:
    """Tests for config parsing."""

    def test_parses_allowed_endpoints_create_hint(self, tmp_path: Path) -> None:
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps(
                {"create": {"allowed-endpoints": ["api.example.com:8443"]}}
            )
        )

        assert parse_config(config_file).create_allowed_endpoints == [
            "api.example.com:8443"
        ]

    def test_rejects_devcontainer_file(self, tmp_path: Path):
        """parse_config accepts only paude.json."""
        config_file = tmp_path / ".devcontainer" / "devcontainer.json"
        config_file.parent.mkdir()
        config_file.write_text("not parsed")

        with pytest.raises(ConfigError, match="Unknown config file type"):
            parse_config(config_file)

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
        assert config.setup_command == "pip install -r requirements.txt"

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
        assert config.setup_command is None
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

    def test_parses_paude_json_create_agents_providers(self, tmp_path: Path):
        """parse_config extracts create agents/providers lists from paude.json."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps(
                {
                    "create": {
                        "agents": ["gascity", "claude"],
                        "providers": ["vertex", "chatgpt"],
                        "agent-providers": {
                            "gascity": "vertex",
                            "claude": "anthropic",
                        },
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert config.create_agents == ["gascity", "claude"]
        assert config.create_providers == ["vertex", "chatgpt"]
        assert config.create_agent_providers == {
            "gascity": "vertex",
            "claude": "anthropic",
        }

    def test_create_agents_default_to_empty(self, tmp_path: Path):
        """create agents/providers default to empty lists when not present."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"create": {"agent": "claude"}}))

        config = parse_config(config_file)
        assert config.create_agents == []
        assert config.create_providers == []
        assert config.create_agent_providers == {}

    def test_parses_paude_json_create_otel_endpoint(self, tmp_path: Path):
        """parse_config extracts otel-endpoint from paude.json create section."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(
            json.dumps(
                {
                    "create": {
                        "otel-endpoint": "http://collector:4318",
                    },
                }
            )
        )

        config = parse_config(config_file)
        assert config.create_otel_endpoint == "http://collector:4318"

    def test_otel_endpoint_defaults_to_none(self, tmp_path: Path):
        """otel-endpoint defaults to None when not in create section."""
        config_file = tmp_path / "paude.json"
        config_file.write_text(json.dumps({"create": {"agent": "claude"}}))

        config = parse_config(config_file)
        assert config.create_otel_endpoint is None

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
        config = PaudeConfig(config_type="paude", base_image="python:3.11-slim")
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
            "jq",
            "findutils",
            "grep",
            "sed",
            "gawk",
            "diffutils",
            "less",
            "file",
            "ripgrep",
            "unzip",
            "zip",
        ]:
            assert pkg in dockerfile, f"Expected package '{pkg}' in Dockerfile"

        # Keep ripgrep available regardless of which supported package manager
        # the user's base image provides.
        assert dockerfile.count("ripgrep") == 4

    def test_dnf_uses_allowerasing(self):
        """generate_workspace_dockerfile uses --allowerasing for dnf to replace coreutils-single."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "--allowerasing" in dockerfile

    def test_pins_runtime_user_uid_gid(self):
        """The runtime user is pinned to uid 1000 / gid 0 so a rebuild never
        drifts the UID out from under an existing /pvc volume (which would make
        the volume unwritable and break the agent with EACCES)."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "useradd --uid 1000 -M -d /home/paude -s /bin/bash -g 0 paude" in (
            dockerfile
        )
        # The volume/home must be group-root so it matches gid 0 ownership.
        assert "chown -R paude:0 /home/paude" in dockerfile
        # A pre-existing base-image user is reused rather than recreated.
        assert "id paude >/dev/null 2>&1" in dockerfile

    def test_dnf_and_yum_install_tmux_from_the_distribution(self):
        """dnf/yum images use the fixed distribution tmux package."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        dnf_section = dockerfile.split("elif command -v dnf", 1)[1].split(
            "elif command -v yum", 1
        )[0]
        yum_section = dockerfile.split("elif command -v yum", 1)[1].split("else", 1)[0]

        assert "bash tmux glibc-langpack-en" in dnf_section
        assert "bash tmux glibc-langpack-en" in yum_section
        assert "TMUX_VERSION" not in dockerfile
        assert "./configure" not in dockerfile
        assert "make -j" not in dockerfile

    def test_copies_tmux_conf(self):
        """generate_workspace_dockerfile includes COPY for tmux.conf."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "COPY --chmod=664 tmux.conf" in dockerfile
        assert ".tmux.conf" in dockerfile

    def test_installs_tini(self):
        """generate_workspace_dockerfile installs tini for zombie reaping."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "/usr/local/bin/tini" in dockerfile
        assert "TINI_VERSION" in dockerfile
        assert "krallin/tini" in dockerfile
        assert "tini --version" in dockerfile
        assert "command -v tini" in dockerfile  # idempotency guard
        assert "tini-static" in dockerfile  # static binary for musl/glibc compat

    def test_preserves_path_across_login_shells(self):
        """generate_workspace_dockerfile writes /etc/profile.d script to preserve PATH."""
        config = PaudeConfig()
        dockerfile = generate_workspace_dockerfile(config)

        assert "/etc/profile.d/paude-path.sh" in dockerfile
        assert "export PATH=" in dockerfile


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

    def test_starts_with_user_root_for_agent_install(self):
        """Dockerfile with include_claude_install has USER root before USER paude.

        This ensures the agent install runs as root, even when the base image
        ends with a non-root user.
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

    def test_includes_packages_with_claude_install(self):
        """generate_pip_install_dockerfile installs packages when layering on default image."""
        config = PaudeConfig(packages=["python3.12-devel", "gcc"])
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=True
        )

        assert "python3.12-devel gcc" in dockerfile
        assert "User-specified packages from paude.json" in dockerfile

    def test_includes_packages_without_claude_install(self):
        """generate_pip_install_dockerfile installs packages in minimal mode."""
        config = PaudeConfig(packages=["vim"])
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=False
        )

        assert "vim" in dockerfile
        assert "User-specified packages from paude.json" in dockerfile

    def test_minimal_dockerfile_has_user_paude(self):
        """Minimal Dockerfile (no claude) still has USER paude."""
        config = PaudeConfig()
        dockerfile = generate_pip_install_dockerfile(
            config, include_claude_install=False
        )

        assert "USER paude" in dockerfile
        lines = dockerfile.split("\n")
        user_lines = [
            (i, line.strip())
            for i, line in enumerate(lines)
            if line.strip().startswith("USER")
        ]

        assert len(user_lines) >= 2, "Expected at least 2 USER lines"
        assert user_lines[0][1] == "USER root"
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
