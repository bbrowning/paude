"""Tests for the agent abstraction module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from paude.agents import get_agent, list_agents
from paude.agents.base import AgentConfig, build_environment_from_config
from paude.agents.claude import ClaudeAgent
from paude.agents.gemini import GeminiAgent


class TestRegistry:
    """Tests for agent registry functions."""

    def test_get_agent_claude(self) -> None:
        agent = get_agent("claude")
        assert isinstance(agent, ClaudeAgent)

    def test_get_agent_returns_new_instance(self) -> None:
        a1 = get_agent("claude")
        a2 = get_agent("claude")
        assert a1 is not a2

    def test_get_agent_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown agent 'nonexistent'"):
            get_agent("nonexistent")

    def test_get_agent_gemini(self) -> None:
        agent = get_agent("gemini")
        assert isinstance(agent, GeminiAgent)

    def test_get_agent_error_lists_available(self) -> None:
        with pytest.raises(ValueError, match="Available: claude, gemini"):
            get_agent("bad")

    def test_list_agents(self) -> None:
        agents = list_agents()
        assert "claude" in agents
        assert "gemini" in agents
        assert agents == sorted(agents)


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_defaults(self) -> None:
        cfg = AgentConfig(
            name="test",
            display_name="Test Agent",
            process_name="test",
            session_name="test",
            install_script="echo hi",
        )
        assert cfg.install_dir == ".local/bin"
        assert cfg.config_dir_name == ".claude"
        assert cfg.config_file_name == ".claude.json"
        assert cfg.yolo_flag == "--dangerously-skip-permissions"
        assert cfg.clear_command == "/clear"
        assert cfg.args_env_var == "PAUDE_AGENT_ARGS"
        assert cfg.skip_install_env_var == "PAUDE_SKIP_AGENT_INSTALL"
        assert cfg.env_vars == {}
        assert cfg.passthrough_env_vars == []
        assert cfg.passthrough_env_prefixes == []
        assert cfg.config_excludes == []
        assert cfg.activity_files == []
        assert cfg.extra_domain_aliases == ["claude"]


class TestClaudeAgentConfig:
    """Tests for ClaudeAgent configuration values."""

    def test_name(self) -> None:
        agent = ClaudeAgent()
        assert agent.config.name == "claude"

    def test_display_name(self) -> None:
        assert ClaudeAgent().config.display_name == "Claude Code"

    def test_process_name(self) -> None:
        assert ClaudeAgent().config.process_name == "claude"

    def test_session_name(self) -> None:
        assert ClaudeAgent().config.session_name == "claude"

    def test_install_script(self) -> None:
        cfg = ClaudeAgent().config
        assert "claude.ai/install.sh" in cfg.install_script

    def test_env_vars(self) -> None:
        cfg = ClaudeAgent().config
        assert cfg.env_vars == {"DISABLE_AUTOUPDATER": "1"}

    def test_config_dir_name(self) -> None:
        assert ClaudeAgent().config.config_dir_name == ".claude"

    def test_config_file_name(self) -> None:
        assert ClaudeAgent().config.config_file_name == ".claude.json"

    def test_yolo_flag(self) -> None:
        assert ClaudeAgent().config.yolo_flag == "--dangerously-skip-permissions"

    def test_clear_command(self) -> None:
        assert ClaudeAgent().config.clear_command == "/clear"

    def test_config_excludes_not_empty(self) -> None:
        cfg = ClaudeAgent().config
        assert len(cfg.config_excludes) > 0
        assert "/projects" in cfg.config_excludes

    def test_passthrough_vars(self) -> None:
        cfg = ClaudeAgent().config
        assert "CLAUDE_CODE_USE_VERTEX" in cfg.passthrough_env_vars

    def test_extra_domain_aliases(self) -> None:
        cfg = ClaudeAgent().config
        assert cfg.extra_domain_aliases == ["claude"]

    def test_passthrough_prefixes(self) -> None:
        cfg = ClaudeAgent().config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes


class TestClaudeAgentDockerfile:
    """Tests for ClaudeAgent.dockerfile_install_lines."""

    def test_returns_list(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_contains_install_command(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "claude.ai/install.sh" in text

    def test_sets_path(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "/home/paude/.local/bin" in text

    def test_disables_autoupdater(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "DISABLE_AUTOUPDATER=1" in text

    def test_uses_container_home(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/custom/home")
        text = "\n".join(lines)
        assert "/custom/home" in text


class TestClaudeAgentLaunchCommand:
    """Tests for ClaudeAgent.launch_command."""

    def test_no_args(self) -> None:
        assert ClaudeAgent().launch_command("") == "claude"

    def test_with_args(self) -> None:
        assert ClaudeAgent().launch_command("--yolo") == "claude --yolo"


class TestClaudeAgentHostConfigMounts:
    """Tests for ClaudeAgent.host_config_mounts."""

    def test_empty_when_no_config(self, tmp_path: Path) -> None:
        mounts = ClaudeAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_mounts_claude_dir(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        mounts = ClaudeAgent().host_config_mounts(tmp_path)
        assert "-v" in mounts
        assert any("/tmp/claude.seed:ro" in m for m in mounts)

    def test_mounts_plugins(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        plugins = claude_dir / "plugins"
        plugins.mkdir()
        mounts = ClaudeAgent().host_config_mounts(tmp_path)
        assert any("plugins" in m and ":ro" in m for m in mounts)

    def test_mounts_claude_json(self, tmp_path: Path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}")
        mounts = ClaudeAgent().host_config_mounts(tmp_path)
        assert any("/tmp/claude.json.seed:ro" in m for m in mounts)


class TestClaudeAgentBuildEnvironment:
    """Tests for ClaudeAgent.build_environment."""

    def test_empty_when_no_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = ClaudeAgent().build_environment()
            assert env == {}

    def test_passes_through_vertex_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLAUDE_CODE_USE_VERTEX": "1", "UNRELATED": "x"},
            clear=True,
        ):
            env = ClaudeAgent().build_environment()
            assert env == {"CLAUDE_CODE_USE_VERTEX": "1"}

    def test_passes_through_prefix_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLOUDSDK_AUTH_TOKEN": "abc", "OTHER": "x"},
            clear=True,
        ):
            env = ClaudeAgent().build_environment()
            assert env == {"CLOUDSDK_AUTH_TOKEN": "abc"}


class TestClaudeAgentSandboxConfig:
    """Tests for ClaudeAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = ClaudeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_contains_trust_config(self) -> None:
        script = ClaudeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "hasCompletedOnboarding" in script
        assert "hasTrustDialogAccepted" in script

    def test_contains_workspace(self) -> None:
        script = ClaudeAgent().apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert "/pvc/workspace" in script

    def test_yolo_flag_in_script(self) -> None:
        script = ClaudeAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "--dangerously-skip-permissions"
        )
        assert "skipDangerousModePermissionPrompt" in script


class TestGeminiAgentConfig:
    """Tests for GeminiAgent configuration values."""

    def test_name(self) -> None:
        assert GeminiAgent().config.name == "gemini"

    def test_display_name(self) -> None:
        assert GeminiAgent().config.display_name == "Gemini CLI"

    def test_process_name(self) -> None:
        assert GeminiAgent().config.process_name == "gemini"

    def test_session_name(self) -> None:
        assert GeminiAgent().config.session_name == "gemini"

    def test_install_script(self) -> None:
        cfg = GeminiAgent().config
        assert "@google/gemini-cli" in cfg.install_script

    def test_config_dir_name(self) -> None:
        assert GeminiAgent().config.config_dir_name == ".gemini"

    def test_config_file_name_is_none(self) -> None:
        assert GeminiAgent().config.config_file_name is None

    def test_yolo_flag(self) -> None:
        assert GeminiAgent().config.yolo_flag == "--yolo"

    def test_clear_command_is_none(self) -> None:
        assert GeminiAgent().config.clear_command is None

    def test_passthrough_vars(self) -> None:
        cfg = GeminiAgent().config
        assert "GOOGLE_CLOUD_PROJECT" in cfg.passthrough_env_vars

    def test_passthrough_prefixes(self) -> None:
        cfg = GeminiAgent().config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes

    def test_extra_domain_aliases(self) -> None:
        cfg = GeminiAgent().config
        assert "gemini" in cfg.extra_domain_aliases
        assert "nodejs" in cfg.extra_domain_aliases

    def test_env_vars_empty(self) -> None:
        assert GeminiAgent().config.env_vars == {}

    def test_config_excludes_empty(self) -> None:
        assert GeminiAgent().config.config_excludes == []

    def test_activity_files_empty(self) -> None:
        assert GeminiAgent().config.activity_files == []


class TestGeminiAgentDockerfile:
    """Tests for GeminiAgent.dockerfile_install_lines."""

    def test_contains_nodejs(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "nodejs" in text

    def test_contains_npm(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "npm" in text

    def test_contains_gemini_cli(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "@google/gemini-cli" in text

    def test_contains_chmod(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "chmod -R g+rwX" in text


class TestGeminiAgentLaunchCommand:
    """Tests for GeminiAgent.launch_command."""

    def test_no_args(self) -> None:
        assert GeminiAgent().launch_command("") == "gemini"

    def test_with_args(self) -> None:
        assert GeminiAgent().launch_command("--flag") == "gemini --flag"


class TestGeminiAgentHostConfigMounts:
    """Tests for GeminiAgent.host_config_mounts."""

    def test_empty_when_no_gemini_dir(self, tmp_path: Path) -> None:
        mounts = GeminiAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_mounts_gemini_dir(self, tmp_path: Path) -> None:
        gemini_dir = tmp_path / ".gemini"
        gemini_dir.mkdir()
        mounts = GeminiAgent().host_config_mounts(tmp_path)
        assert "-v" in mounts
        assert any("/tmp/gemini.seed:ro" in m for m in mounts)

    def test_no_config_file_mount(self, tmp_path: Path) -> None:
        gemini_json = tmp_path / ".gemini.json"
        gemini_json.write_text("{}")
        mounts = GeminiAgent().host_config_mounts(tmp_path)
        assert not any("gemini.json" in m for m in mounts)


class TestGeminiAgentBuildEnvironment:
    """Tests for GeminiAgent.build_environment."""

    def test_empty_when_no_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = GeminiAgent().build_environment()
            assert env == {}

    def test_passes_through_google_cloud_project(self) -> None:
        with patch.dict(
            "os.environ",
            {"GOOGLE_CLOUD_PROJECT": "my-project", "UNRELATED": "x"},
            clear=True,
        ):
            env = GeminiAgent().build_environment()
            assert env == {"GOOGLE_CLOUD_PROJECT": "my-project"}

    def test_passes_through_cloudsdk_auth_prefix(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLOUDSDK_AUTH_TOKEN": "abc", "OTHER": "x"},
            clear=True,
        ):
            env = GeminiAgent().build_environment()
            assert env == {"CLOUDSDK_AUTH_TOKEN": "abc"}


class TestGeminiAgentSandboxConfig:
    """Tests for GeminiAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = GeminiAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_contains_trusted_folders_json(self) -> None:
        script = GeminiAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "trustedFolders.json" in script

    def test_uses_jq_for_trust(self) -> None:
        script = GeminiAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "jq" in script
        assert "TRUST_FOLDER" in script

    def test_workspace_path_parameterized(self) -> None:
        script = GeminiAgent().apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert "/pvc/workspace" in script

    def test_home_path_parameterized(self) -> None:
        script = GeminiAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.gemini" in script


class TestBuildEnvironmentFromConfig:
    """Tests for the shared build_environment_from_config helper."""

    def test_collects_passthrough_vars(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["MY_VAR"],
            passthrough_env_prefixes=[],
        )
        with patch.dict("os.environ", {"MY_VAR": "val", "OTHER": "x"}, clear=True):
            env = build_environment_from_config(config)
            assert env == {"MY_VAR": "val"}

    def test_collects_prefix_vars(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=[],
            passthrough_env_prefixes=["MY_PREFIX_"],
        )
        with patch.dict(
            "os.environ",
            {"MY_PREFIX_FOO": "a", "MY_PREFIX_BAR": "b", "OTHER": "x"},
            clear=True,
        ):
            env = build_environment_from_config(config)
            assert env == {"MY_PREFIX_FOO": "a", "MY_PREFIX_BAR": "b"}

    def test_empty_when_no_matches(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["MISSING"],
            passthrough_env_prefixes=["NOPE_"],
        )
        with patch.dict("os.environ", {"OTHER": "x"}, clear=True):
            env = build_environment_from_config(config)
            assert env == {}
