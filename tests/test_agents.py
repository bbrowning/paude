"""Tests for the agent abstraction module."""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from paude.agents import get_agent, get_agent_composition, get_agents, list_agents
from paude.agents.base import (
    AgentComposition,
    AgentConfig,
    build_environment_from_config,
    build_secret_environment_from_config,
    compose_dockerfile_install_lines,
    nodejs_prereq_install_lines,
    pipefail_install_lines,
)
from paude.agents.claude import ClaudeAgent
from paude.agents.codex import (
    CODEX_CHATGPT_PROFILE_NAME,
    SYNTHETIC_CODEX_PROFILE_TOML,
    CodexAgent,
)
from paude.agents.cursor import CursorAgent
from paude.agents.gascity import GascityAgent
from paude.agents.gemini import GeminiAgent
from paude.agents.openclaw import OpenClawAgent
from paude.agents.opencode import OpenCodeAgent


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

    def test_get_agent_codex(self) -> None:
        agent = get_agent("codex")
        assert isinstance(agent, CodexAgent)

    def test_get_agent_cursor(self) -> None:
        agent = get_agent("cursor")
        assert isinstance(agent, CursorAgent)

    def test_get_agent_gemini(self) -> None:
        agent = get_agent("gemini")
        assert isinstance(agent, GeminiAgent)

    def test_get_agent_gascity(self) -> None:
        agent = get_agent("gascity")
        assert isinstance(agent, GascityAgent)

    def test_get_agent_opencode(self) -> None:
        agent = get_agent("opencode")
        assert isinstance(agent, OpenCodeAgent)

    def test_get_agent_openclaw(self) -> None:
        agent = get_agent("openclaw")
        assert isinstance(agent, OpenClawAgent)

    def test_get_agent_error_lists_available(self) -> None:
        with pytest.raises(
            ValueError,
            match="Available: claude, codex, cursor, gascity, gemini, openclaw, opencode",
        ):
            get_agent("bad")

    def test_list_agents(self) -> None:
        agents = list_agents()
        assert "claude" in agents
        assert "codex" in agents
        assert "cursor" in agents
        assert "gascity" in agents
        assert "gemini" in agents
        assert "opencode" in agents
        assert "openclaw" in agents
        assert agents == sorted(agents)


class TestGetAgents:
    """Tests for get_agents() composition, expansion, dedup, and ordering."""

    def test_single_agent_returns_itself(self) -> None:
        composition = get_agents(["claude"])
        assert isinstance(composition, AgentComposition)
        assert composition.names == ["claude"]

    def test_primary_is_first_requested(self) -> None:
        composition = get_agents(["gascity", "codex"])
        assert composition.primary.config.name == "gascity"

    def test_primary_is_first_element_of_install_set(self) -> None:
        composition = get_agents(["codex", "gascity"])
        assert composition.primary is composition.agents[0]
        assert composition.primary.config.name == "codex"

    def test_expands_bundled_agents(self) -> None:
        # gascity bundles claude + gemini.
        composition = get_agents(["gascity"])
        assert composition.names == ["gascity", "claude", "gemini"]

    def test_explicit_multi_agent_list_is_exact(self) -> None:
        composition = get_agents(["gascity", "claude", "codex"], include_bundled=False)
        assert composition.names == ["gascity", "claude", "codex"]
        assert "gemini" not in composition.names

    def test_acceptance_gascity_plus_codex(self) -> None:
        composition = get_agents(["gascity", "codex"])
        assert composition.names == ["gascity", "claude", "gemini", "codex"]
        assert composition.primary.config.name == "gascity"

    def test_dedups_explicitly_requested_bundled_agent(self) -> None:
        # claude is both explicitly requested and bundled by gascity.
        composition = get_agents(["gascity", "claude"])
        assert composition.names == ["gascity", "claude", "gemini"]

    def test_dedup_preserves_first_seen_order(self) -> None:
        composition = get_agents(["claude", "gascity"])
        # claude first (explicit), then gascity, then gascity's remaining
        # bundled agent (gemini); claude is not repeated.
        assert composition.names == ["claude", "gascity", "gemini"]

    def test_agents_are_instances(self) -> None:
        composition = get_agents(["gascity"])
        assert isinstance(composition.agents[0], GascityAgent)
        assert isinstance(composition.agents[1], ClaudeAgent)
        assert isinstance(composition.agents[2], GeminiAgent)

    def test_each_agent_gets_its_own_default_provider(self) -> None:
        composition = get_agents(["gascity"])
        providers = {a.config.name: a.config.provider for a in composition.agents}
        assert providers["gascity"] == "vertex"
        assert providers["claude"] == "vertex"
        assert providers["gemini"] == "google"

    def test_provider_override_applies_per_agent(self) -> None:
        composition = get_agents(["codex", "gemini"], providers={"codex": "openai"})
        providers = {a.config.name: a.config.provider for a in composition.agents}
        assert providers["codex"] == "openai"
        # gemini keeps its own default, unaffected by the codex override.
        assert providers["gemini"] == "google"

    def test_empty_names_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one agent name"):
            get_agents([])

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown agent 'bogus'"):
            get_agents(["bogus"])


class TestGetAgentComposition:
    """Tests for get_agent_composition()'s verbatim-primary contract."""

    def test_preserves_custom_instance_verbatim(self) -> None:
        # "anthropic" differs from claude's own default provider ("vertex"),
        # so this proves the instance is used as-is rather than rebuilt from
        # the registry with its default provider.
        agent = get_agent("claude", provider="anthropic")
        composition = get_agent_composition(agent)
        assert composition.primary is agent
        assert composition.primary.config.provider == "anthropic"

    def test_single_agent_with_no_bundles_composes_to_itself(self) -> None:
        agent = get_agent("codex")
        composition = get_agent_composition(agent)
        assert composition.agents == [agent]

    def test_expands_bundled_agents_around_existing_instance(self) -> None:
        agent = get_agent("gascity")
        composition = get_agent_composition(agent)
        assert composition.primary is agent
        assert composition.names == ["gascity", "claude", "gemini"]
        # Bundled agents are freshly resolved from the registry (not the
        # same objects as get_agent("gascity")'s own bundled dependencies).
        assert isinstance(composition.agents[1], ClaudeAgent)
        assert isinstance(composition.agents[2], GeminiAgent)


class TestComposeDockerfileInstallLines:
    """Tests for compose_dockerfile_install_lines() ordering and dedup."""

    def test_concatenates_in_order(self) -> None:
        agents = [get_agent("codex"), get_agent("claude")]
        lines = compose_dockerfile_install_lines(agents, "/home/paude")
        text = "\n".join(lines)
        assert "Codex CLI" in text
        assert "claude.ai/install.sh" in text
        # codex comes before claude in the requested order
        assert text.index("Codex CLI") < text.index("claude.ai/install.sh")

    def test_dedups_shared_node_prereq(self) -> None:
        agents = [get_agent("gemini"), get_agent("gascity")]
        lines = compose_dockerfile_install_lines(agents, "/home/paude")
        node_installs = [line for line in lines if "dnf install -y nodejs npm" in line]
        assert len(node_installs) == 1

    def test_preserves_distinct_tool_installs(self) -> None:
        # Non-prereq installs (npm of a specific CLI) are never deduped.
        agents = [get_agent("gemini"), get_agent("gemini")]
        lines = compose_dockerfile_install_lines(agents, "/home/paude")
        gemini_installs = [line for line in lines if "@google/gemini-cli" in line]
        assert len(gemini_installs) == 2

    def test_ends_with_canonical_user_workdir(self) -> None:
        agents = [get_agent("claude")]
        lines = compose_dockerfile_install_lines(agents, "/custom/home")
        assert lines[-2:] == ["USER paude", "WORKDIR /custom/home"]

    def test_no_trailing_duplicate_workdir(self) -> None:
        # gemini ends with its own USER/WORKDIR; the composer should not leave
        # two WORKDIR lines back-to-back at the end.
        agents = [get_agent("gemini")]
        lines = compose_dockerfile_install_lines(agents, "/home/paude")
        workdir_lines = [i for i, line in enumerate(lines) if "WORKDIR" in line]
        assert workdir_lines == [len(lines) - 1]


class TestNodejsPrereqInstallLines:
    """Tests for the shared Node.js prerequisite helper."""

    def test_installs_node_as_root(self) -> None:
        lines = nodejs_prereq_install_lines()
        assert lines[0] == "USER root"
        assert "nodejs" in lines[1]
        assert "npm" in lines[1]

    def test_identical_across_calls(self) -> None:
        # Dedup relies on byte-identical output.
        assert nodejs_prereq_install_lines() == nodejs_prereq_install_lines()


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
        assert cfg.activity_files == []
        assert cfg.extra_domain_aliases == ["claude"]
        assert cfg.exposed_ports == []
        assert cfg.default_base_image is None
        assert cfg.extra_persistent_dir_names == []
        assert cfg.persistent_dir_names == [".claude"]


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
        assert cfg.env_vars == {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "NODE_USE_ENV_PROXY": "1",
        }

    def test_config_dir_name(self) -> None:
        assert ClaudeAgent().config.config_dir_name == ".claude"

    def test_config_file_name(self) -> None:
        assert ClaudeAgent().config.config_file_name == ".claude.json"

    def test_yolo_flag(self) -> None:
        assert ClaudeAgent().config.yolo_flag == "--dangerously-skip-permissions"

    def test_clear_command(self) -> None:
        assert ClaudeAgent().config.clear_command == "/clear"

    def test_passthrough_vars(self) -> None:
        cfg = ClaudeAgent().config
        assert "ANTHROPIC_VERTEX_PROJECT_ID" in cfg.passthrough_env_vars

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

    def test_uses_container_home(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/custom/home")
        text = "\n".join(lines)
        assert "/custom/home" in text

    def test_pipefail_shell(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "pipefail" in text

    def test_binary_verification(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "test -x /home/paude/.local/bin/claude" in text

    def test_shell_reset(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        assert 'SHELL ["/bin/sh", "-c"]' in lines

    def test_error_message(self) -> None:
        lines = ClaudeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "ERROR" in text
        assert "installation failed" in text


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
        assert mounts == []


class TestClaudeAgentBuildEnvironment:
    """Tests for ClaudeAgent.build_environment."""

    def test_includes_static_env_vars_when_no_host_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = ClaudeAgent().build_environment()
            assert env == {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "NODE_USE_ENV_PROXY": "1",
            }

    def test_passes_through_vertex_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_VERTEX_PROJECT_ID": "proj-1", "UNRELATED": "x"},
            clear=True,
        ):
            env = ClaudeAgent().build_environment()
            assert env == {
                "ANTHROPIC_VERTEX_PROJECT_ID": "proj-1",
                "GOOGLE_CLOUD_PROJECT": "proj-1",
                "CLAUDE_CODE_USE_VERTEX": "1",
                "NODE_USE_ENV_PROXY": "1",
            }

    def test_passes_through_prefix_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"CLOUDSDK_AUTH_TOKEN": "abc", "OTHER": "x"},
            clear=True,
        ):
            env = ClaudeAgent().build_environment()
            assert env == {
                "CLOUDSDK_AUTH_TOKEN": "abc",
                "CLAUDE_CODE_USE_VERTEX": "1",
                "NODE_USE_ENV_PROXY": "1",
            }


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
            "/home/paude", "/workspace", "", yolo=True
        )
        assert "skipDangerousModePermissionPrompt" in script

    def test_no_yolo_flag_without_yolo(self) -> None:
        script = ClaudeAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "--dangerously-skip-permissions"
        )
        assert "skipDangerousModePermissionPrompt" not in script


class TestCodexAgentConfig:
    """Tests for CodexAgent configuration values."""

    def test_name(self) -> None:
        assert CodexAgent().config.name == "codex"

    def test_display_name(self) -> None:
        assert CodexAgent().config.display_name == "Codex CLI"

    def test_process_name(self) -> None:
        assert CodexAgent().config.process_name == "codex"

    def test_session_name(self) -> None:
        assert CodexAgent().config.session_name == "codex"

    def test_install_script(self) -> None:
        cfg = CodexAgent().config
        assert "openai/codex" in cfg.install_script

    def test_install_script_uses_latest_stable_release(self) -> None:
        cfg = CodexAgent().config
        assert "releases/latest/download" in cfg.install_script

    def test_config_dir_name(self) -> None:
        assert CodexAgent().config.config_dir_name == ".codex"

    def test_config_file_name_is_none(self) -> None:
        assert CodexAgent().config.config_file_name is None

    def test_persistent_dirs(self) -> None:
        assert CodexAgent().config.persistent_dir_names == [".codex", ".agents"]

    def test_yolo_flag(self) -> None:
        assert (
            CodexAgent().config.yolo_flag
            == "--dangerously-bypass-approvals-and-sandbox"
        )

    def test_clear_command_is_none(self) -> None:
        assert CodexAgent().config.clear_command is None

    @pytest.mark.parametrize("kwargs", [{}, {"provider": "chatgpt"}])
    def test_secret_env_vars_chatgpt_no_api_key(self, kwargs: dict[str, str]) -> None:
        """Default and explicit chatgpt provider are proxy-managed OAuth, not an API key."""
        assert CodexAgent(**kwargs).config.secret_env_vars == []

    def test_secret_env_vars_openai(self) -> None:
        cfg = CodexAgent(provider="openai").config
        assert "OPENAI_API_KEY" in cfg.secret_env_vars

    def test_passthrough_vars_empty(self) -> None:
        assert CodexAgent().config.passthrough_env_vars == []

    def test_passthrough_prefixes_empty(self) -> None:
        assert CodexAgent().config.passthrough_env_prefixes == []

    def test_extra_domain_aliases_openai_excludes_chatgpt(self) -> None:
        """Plain API-key sessions don't need chatgpt.com/auth.openai.com allowlisted."""
        assert CodexAgent(provider="openai").config.extra_domain_aliases == []
        assert CodexAgent(provider="openai").config.required_domain_aliases == []

    @pytest.mark.parametrize("kwargs", [{}, {"provider": "chatgpt"}])
    def test_extra_domain_aliases_chatgpt(self, kwargs: dict[str, str]) -> None:
        """Default and explicit chatgpt provider both need chatgpt.com allowlisted."""
        cfg = CodexAgent(**kwargs).config
        assert cfg.extra_domain_aliases == ["chatgpt"]
        assert cfg.required_domain_aliases == ["chatgpt"]

    def test_chatgpt_provider_is_resolved_provider(self) -> None:
        assert CodexAgent(provider="chatgpt").config.provider == "chatgpt"

    def test_env_vars_empty(self) -> None:
        assert CodexAgent().config.env_vars == {"CODEX_HOME": "/home/paude/.codex"}

    def test_chatgpt_profile_disables_websockets(self) -> None:
        assert f'model_provider = "{CODEX_CHATGPT_PROFILE_NAME}"' in (
            SYNTHETIC_CODEX_PROFILE_TOML
        )
        assert 'base_url = "https://chatgpt.com/backend-api/codex"' in (
            SYNTHETIC_CODEX_PROFILE_TOML
        )
        assert "requires_openai_auth = true" in SYNTHETIC_CODEX_PROFILE_TOML
        assert "supports_websockets = false" in SYNTHETIC_CODEX_PROFILE_TOML

    def test_chatgpt_profile_disables_apps(self) -> None:
        profile = tomllib.loads(SYNTHETIC_CODEX_PROFILE_TOML)
        assert profile["features"]["apps"] is False

    def test_activity_files_empty(self) -> None:
        assert CodexAgent().config.activity_files == []

    def test_exposed_ports_empty(self) -> None:
        assert CodexAgent().config.exposed_ports == []

    def test_default_base_image_is_none(self) -> None:
        assert CodexAgent().config.default_base_image is None


class TestCodexAgentDockerfile:
    """Tests for CodexAgent.dockerfile_install_lines."""

    def test_returns_list(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_contains_install_url(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "openai/codex" in text

    def test_contains_version(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "releases/latest/download" in text

    def test_contains_arch_detection(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "uname -m" in text

    def test_contains_x86_64_arch(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "x86_64-unknown-linux-musl" in text

    def test_contains_aarch64_arch(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "aarch64-unknown-linux-musl" in text

    def test_sets_path(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "/home/paude/.local/bin" in text

    def test_uses_container_home(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/custom/home")
        text = "\n".join(lines)
        assert "/custom/home" in text

    def test_pipefail_shell(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "pipefail" in text

    def test_binary_verification(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "test -x /home/paude/.local/bin/codex" in text

    def test_shell_reset(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        assert 'SHELL ["/bin/sh", "-c"]' in lines

    def test_error_message(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "ERROR" in text
        assert "installation failed" in text

    def test_contains_umask(self) -> None:
        lines = CodexAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "umask 0002" in text


class TestCodexAgentLaunchCommand:
    """Tests for CodexAgent.launch_command."""

    def test_no_args(self) -> None:
        assert CodexAgent().launch_command("") == "codex"

    def test_with_args(self) -> None:
        assert CodexAgent().launch_command("--flag") == "codex --flag"


class TestCodexAgentHostConfigMounts:
    """Tests for CodexAgent.host_config_mounts."""

    def test_empty_when_no_config(self, tmp_path: Path) -> None:
        mounts = CodexAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_empty_when_dir_exists(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        mounts = CodexAgent().host_config_mounts(tmp_path)
        assert mounts == []


class TestCodexAgentBuildEnvironment:
    """Tests for CodexAgent.build_environment."""

    def test_empty_when_no_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = CodexAgent().build_environment()
            assert env == {"CODEX_HOME": "/home/paude/.codex"}

    def test_does_not_include_secret_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = CodexAgent().build_environment()
            assert "OPENAI_API_KEY" not in env

    def test_secret_env_collects_openai_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = build_secret_environment_from_config(
                CodexAgent(provider="openai").config
            )
            assert env == {"OPENAI_API_KEY": "sk-test"}


class TestCodexAgentSandboxConfig:
    """Tests for CodexAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = CodexAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_creates_config_dir(self) -> None:
        script = CodexAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert ".codex" in script

    def test_home_path_parameterized(self) -> None:
        script = CodexAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.codex" in script

    def test_yolo_does_not_change_config(self) -> None:
        script_normal = CodexAgent().apply_sandbox_config(
            "/home/paude", "/workspace", ""
        )
        script_yolo = CodexAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert script_normal == script_yolo


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

    def test_persistent_dirs(self) -> None:
        assert GeminiAgent().config.persistent_dir_names == [".gemini", ".agents"]

    def test_yolo_flag(self) -> None:
        assert GeminiAgent().config.yolo_flag == "--yolo"

    def test_clear_command(self) -> None:
        assert GeminiAgent().config.clear_command == "/clear"

    def test_passthrough_vars(self) -> None:
        cfg = GeminiAgent().config
        assert "GOOGLE_CLOUD_PROJECT" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_LOCATION" in cfg.passthrough_env_vars
        assert "CLOUD_ML_REGION" in cfg.passthrough_env_vars

    def test_passthrough_prefixes(self) -> None:
        cfg = GeminiAgent().config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes

    def test_extra_domain_aliases(self) -> None:
        cfg = GeminiAgent().config
        assert "gemini" in cfg.extra_domain_aliases
        assert "nodejs" in cfg.extra_domain_aliases

    def test_env_vars_empty(self) -> None:
        assert GeminiAgent().config.env_vars == {}

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

    def test_no_chmod(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "chmod" not in text

    def test_no_pipefail(self) -> None:
        lines = GeminiAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "pipefail" not in text


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
        assert mounts == []

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


class TestCursorAgentConfig:
    """Tests for CursorAgent configuration values."""

    def test_name(self) -> None:
        assert CursorAgent().config.name == "cursor"

    def test_display_name(self) -> None:
        assert CursorAgent().config.display_name == "Cursor"

    def test_process_name(self) -> None:
        assert CursorAgent().config.process_name == "agent"

    def test_session_name(self) -> None:
        assert CursorAgent().config.session_name == "cursor"

    def test_install_script(self) -> None:
        cfg = CursorAgent().config
        assert "cursor.com/install" in cfg.install_script

    def test_config_dir_name(self) -> None:
        assert CursorAgent().config.config_dir_name == ".cursor"

    def test_config_file_name_is_none(self) -> None:
        assert CursorAgent().config.config_file_name is None

    def test_persistent_dirs(self) -> None:
        assert CursorAgent().config.persistent_dir_names == [
            ".cursor",
            ".config/cursor",
        ]

    def test_yolo_flag(self) -> None:
        assert CursorAgent().config.yolo_flag == "--yolo"

    def test_clear_command(self) -> None:
        assert CursorAgent().config.clear_command == "/clear"

    def test_passthrough_vars_empty(self) -> None:
        cfg = CursorAgent().config
        assert cfg.passthrough_env_vars == []

    def test_secret_env_vars(self) -> None:
        cfg = CursorAgent().config
        assert "CURSOR_API_KEY" in cfg.secret_env_vars

    def test_passthrough_prefixes_empty(self) -> None:
        assert CursorAgent().config.passthrough_env_prefixes == []

    def test_extra_domain_aliases(self) -> None:
        cfg = CursorAgent().config
        assert cfg.extra_domain_aliases == ["cursor"]

    def test_env_vars(self) -> None:
        cfg = CursorAgent().config
        assert cfg.env_vars == {
            "APPIMAGE_EXTRACT_AND_RUN": "1",
            "NODE_USE_ENV_PROXY": "1",
        }

    def test_activity_files_empty(self) -> None:
        assert CursorAgent().config.activity_files == []


class TestCursorAgentDockerfile:
    """Tests for CursorAgent.dockerfile_install_lines."""

    def test_contains_install_command(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "cursor.com/install" in text

    def test_contains_appimage_env(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "APPIMAGE_EXTRACT_AND_RUN=1" in text

    def test_contains_node_proxy_env(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "NODE_USE_ENV_PROXY=1" in text

    def test_sets_path(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "/home/paude/.local/bin" in text

    def test_contains_umask(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "umask 0002" in text

    def test_uses_container_home(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/custom/home")
        text = "\n".join(lines)
        assert "/custom/home" in text

    def test_pipefail_shell(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "pipefail" in text

    def test_binary_verification(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "test -x /home/paude/.local/bin/agent" in text

    def test_shell_reset(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        assert 'SHELL ["/bin/sh", "-c"]' in lines

    def test_error_message(self) -> None:
        lines = CursorAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "ERROR" in text
        assert "installation failed" in text


class TestCursorAgentLaunchCommand:
    """Tests for CursorAgent.launch_command."""

    def test_no_args(self) -> None:
        assert CursorAgent().launch_command("") == "agent"

    def test_with_args(self) -> None:
        assert CursorAgent().launch_command("--yolo") == "agent --yolo"


class TestCursorAgentHostConfigMounts:
    """Tests for CursorAgent.host_config_mounts."""

    def test_empty_when_no_config(self, tmp_path: Path) -> None:
        mounts = CursorAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_empty_when_dir_exists_but_no_cli_config(self, tmp_path: Path) -> None:
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        mounts = CursorAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_mounts_auth_json_when_exists(self, tmp_path: Path) -> None:
        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text("{}")
        mounts = CursorAgent().host_config_mounts(tmp_path)
        assert "-v" in mounts
        assert any("/tmp/cursor-auth.seed:ro" in m for m in mounts)

    def test_no_auth_json_mount_when_missing(self, tmp_path: Path) -> None:
        mounts = CursorAgent().host_config_mounts(tmp_path)
        assert not any("cursor-auth.seed" in m for m in mounts)

    def test_mounts_only_auth_json_when_both_exist(self, tmp_path: Path) -> None:
        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli-config.json").write_text("{}")
        config_cursor = tmp_path / ".config" / "cursor"
        config_cursor.mkdir(parents=True)
        (config_cursor / "auth.json").write_text("{}")
        mounts = CursorAgent().host_config_mounts(tmp_path)
        assert not any("cursor-cli-config.seed" in m for m in mounts)
        assert any("/tmp/cursor-auth.seed:ro" in m for m in mounts)


class TestCursorAgentBuildEnvironment:
    """Tests for CursorAgent.build_environment."""

    def test_includes_static_env_vars_when_no_host_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = CursorAgent().build_environment()
            assert env == {"APPIMAGE_EXTRACT_AND_RUN": "1", "NODE_USE_ENV_PROXY": "1"}

    def test_does_not_include_secret_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"CURSOR_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = CursorAgent().build_environment()
            assert "CURSOR_API_KEY" not in env

    def test_secret_env_collects_cursor_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"CURSOR_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = build_secret_environment_from_config(CursorAgent().config)
            assert env == {"CURSOR_API_KEY": "sk-test"}


class TestCursorAgentSandboxConfig:
    """Tests for CursorAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_contains_cli_config_json(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "cli-config.json" in script

    def test_uses_jq(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "jq" in script

    def test_home_path_parameterized(self) -> None:
        script = CursorAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.cursor" in script

    def test_copies_auth_json_from_podman_seed(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "/tmp/cursor-auth.seed" in script
        assert ".config/cursor/auth.json" in script

    def test_forces_http1_for_agent_inference(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "useHttp1ForAgent" in script
        assert '"network"' in script

    def test_contains_workspace_trust_file(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert ".workspace-trusted" in script
        assert "workspacePath" in script

    def test_workspace_trust_uses_workspace_param(self) -> None:
        script = CursorAgent().apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert "/pvc/workspace" in script
        assert "workspacePath" in script


class TestPipefailInstallLines:
    """Tests for the shared pipefail_install_lines helper."""

    def _config(self) -> AgentConfig:
        return AgentConfig(
            name="test",
            display_name="Test Agent",
            process_name="testagent",
            session_name="test",
            install_script="curl -fsSL https://example.com/install.sh | bash",
        )

    def test_sets_pipefail_shell(self) -> None:
        lines = pipefail_install_lines(self._config(), "/home/paude")
        assert 'SHELL ["/bin/bash", "-o", "pipefail", "-c"]' in lines

    def test_resets_shell(self) -> None:
        lines = pipefail_install_lines(self._config(), "/home/paude")
        assert 'SHELL ["/bin/sh", "-c"]' in lines

    def test_verifies_binary(self) -> None:
        lines = pipefail_install_lines(self._config(), "/home/paude")
        text = "\n".join(lines)
        assert "test -x /home/paude/.local/bin/testagent" in text

    def test_error_message(self) -> None:
        lines = pipefail_install_lines(self._config(), "/home/paude")
        text = "\n".join(lines)
        assert "ERROR" in text
        assert "installation failed" in text

    def test_uses_display_name_in_error(self) -> None:
        lines = pipefail_install_lines(self._config(), "/home/paude")
        text = "\n".join(lines)
        assert "Test Agent" in text

    def test_uses_container_home(self) -> None:
        lines = pipefail_install_lines(self._config(), "/custom/home")
        text = "\n".join(lines)
        assert "/custom/home/.local/bin/testagent" in text


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

    def test_excludes_secret_vars_from_passthrough(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["PUBLIC_VAR", "SECRET_VAR"],
            secret_env_vars=["SECRET_VAR"],
        )
        with patch.dict(
            "os.environ",
            {"PUBLIC_VAR": "pub", "SECRET_VAR": "sec"},
            clear=True,
        ):
            env = build_environment_from_config(config)
            assert env == {"PUBLIC_VAR": "pub"}
            assert "SECRET_VAR" not in env

    def test_excludes_secret_vars_from_prefix_passthrough(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=[],
            passthrough_env_prefixes=["MY_"],
            secret_env_vars=["MY_SECRET"],
        )
        with patch.dict(
            "os.environ",
            {"MY_PUBLIC": "pub", "MY_SECRET": "sec"},
            clear=True,
        ):
            env = build_environment_from_config(config)
            assert env == {"MY_PUBLIC": "pub"}
            assert "MY_SECRET" not in env

    def test_syncs_cloud_ml_region_to_google_cloud_location(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION"],
        )
        with patch.dict("os.environ", {"CLOUD_ML_REGION": "us-central1"}, clear=True):
            env = build_environment_from_config(config)
            assert env["CLOUD_ML_REGION"] == "us-central1"
            assert env["GOOGLE_CLOUD_LOCATION"] == "us-central1"

    def test_syncs_google_cloud_location_to_cloud_ml_region(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION"],
        )
        with patch.dict(
            "os.environ", {"GOOGLE_CLOUD_LOCATION": "europe-west1"}, clear=True
        ):
            env = build_environment_from_config(config)
            assert env["GOOGLE_CLOUD_LOCATION"] == "europe-west1"
            assert env["CLOUD_ML_REGION"] == "europe-west1"

    def test_no_sync_when_only_one_var_in_passthrough(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["CLOUD_ML_REGION"],
        )
        with patch.dict("os.environ", {"CLOUD_ML_REGION": "us-central1"}, clear=True):
            env = build_environment_from_config(config)
            assert env == {"CLOUD_ML_REGION": "us-central1"}
            assert "GOOGLE_CLOUD_LOCATION" not in env

    def test_syncs_google_cloud_location_to_vertex_location(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["GOOGLE_CLOUD_LOCATION", "VERTEX_LOCATION"],
        )
        with patch.dict(
            "os.environ", {"GOOGLE_CLOUD_LOCATION": "us-east1"}, clear=True
        ):
            env = build_environment_from_config(config)
            assert env["GOOGLE_CLOUD_LOCATION"] == "us-east1"
            assert env["VERTEX_LOCATION"] == "us-east1"

    def test_syncs_vertex_location_to_google_cloud_location(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=["GOOGLE_CLOUD_LOCATION", "VERTEX_LOCATION"],
        )
        with patch.dict("os.environ", {"VERTEX_LOCATION": "europe-west4"}, clear=True):
            env = build_environment_from_config(config)
            assert env["VERTEX_LOCATION"] == "europe-west4"
            assert env["GOOGLE_CLOUD_LOCATION"] == "europe-west4"

    def test_syncs_cloud_ml_region_chains_to_vertex_location(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=[
                "GOOGLE_CLOUD_LOCATION",
                "CLOUD_ML_REGION",
                "VERTEX_LOCATION",
            ],
        )
        with patch.dict("os.environ", {"CLOUD_ML_REGION": "us-central1"}, clear=True):
            env = build_environment_from_config(config)
            assert env["CLOUD_ML_REGION"] == "us-central1"
            assert env["GOOGLE_CLOUD_LOCATION"] == "us-central1"
            assert env["VERTEX_LOCATION"] == "us-central1"

    def test_syncs_google_cloud_project_to_anthropic_vertex_project_id(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=[
                "GOOGLE_CLOUD_PROJECT",
                "ANTHROPIC_VERTEX_PROJECT_ID",
            ],
        )
        with patch.dict(
            "os.environ", {"GOOGLE_CLOUD_PROJECT": "my-project"}, clear=True
        ):
            env = build_environment_from_config(config)
            assert env["GOOGLE_CLOUD_PROJECT"] == "my-project"
            assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "my-project"

    def test_syncs_anthropic_vertex_project_id_to_google_cloud_project(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            passthrough_env_vars=[
                "GOOGLE_CLOUD_PROJECT",
                "ANTHROPIC_VERTEX_PROJECT_ID",
            ],
        )
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_VERTEX_PROJECT_ID": "vertex-proj"},
            clear=True,
        ):
            env = build_environment_from_config(config)
            assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "vertex-proj"
            assert env["GOOGLE_CLOUD_PROJECT"] == "vertex-proj"


class TestBuildSecretEnvironmentFromConfig:
    """Tests for the build_secret_environment_from_config helper."""

    def test_collects_secret_vars(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            secret_env_vars=["MY_SECRET"],
        )
        with patch.dict("os.environ", {"MY_SECRET": "val", "OTHER": "x"}, clear=True):
            env = build_secret_environment_from_config(config)
            assert env == {"MY_SECRET": "val"}

    def test_empty_when_no_matches(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
            secret_env_vars=["MISSING"],
        )
        with patch.dict("os.environ", {"OTHER": "x"}, clear=True):
            env = build_secret_environment_from_config(config)
            assert env == {}

    def test_empty_when_no_secret_vars_defined(self) -> None:
        config = AgentConfig(
            name="test",
            display_name="Test",
            process_name="test",
            session_name="test",
            install_script="echo hi",
        )
        with patch.dict("os.environ", {"SOME_VAR": "x"}, clear=True):
            env = build_secret_environment_from_config(config)
            assert env == {}


class TestOpenCodeAgentConfig:
    """Tests for OpenCodeAgent configuration values."""

    def test_name(self) -> None:
        assert OpenCodeAgent().config.name == "opencode"

    def test_display_name(self) -> None:
        assert OpenCodeAgent().config.display_name == "OpenCode"

    def test_process_name(self) -> None:
        assert OpenCodeAgent().config.process_name == "opencode"

    def test_session_name(self) -> None:
        assert OpenCodeAgent().config.session_name == "opencode"

    def test_install_script(self) -> None:
        cfg = OpenCodeAgent().config
        assert "opencode.ai/install" in cfg.install_script

    def test_config_dir_name(self) -> None:
        assert OpenCodeAgent().config.config_dir_name == ".config/opencode"

    def test_config_file_name_is_none(self) -> None:
        assert OpenCodeAgent().config.config_file_name is None

    def test_persistent_dirs(self) -> None:
        assert OpenCodeAgent().config.persistent_dir_names == [
            ".config/opencode",
            ".local/share/opencode",
            ".local/state/opencode",
        ]

    def test_yolo_flag(self) -> None:
        assert OpenCodeAgent().config.yolo_flag == "--auto"

    def test_clear_command_is_none(self) -> None:
        assert OpenCodeAgent().config.clear_command is None

    def test_extra_domain_aliases(self) -> None:
        assert OpenCodeAgent().config.extra_domain_aliases == ["opencode"]

    def test_extra_domain_aliases_chatgpt(self) -> None:
        cfg = OpenCodeAgent(provider="chatgpt").config
        assert "chatgpt" in cfg.extra_domain_aliases
        assert "opencode" in cfg.extra_domain_aliases
        assert cfg.required_domain_aliases == ["chatgpt"]

    def test_extra_domain_aliases_non_chatgpt_no_required(self) -> None:
        assert OpenCodeAgent().config.required_domain_aliases == []
        assert OpenCodeAgent(provider="openai").config.required_domain_aliases == []

    def test_env_vars(self) -> None:
        assert OpenCodeAgent().config.env_vars == {
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
        }

    def test_activity_files_empty(self) -> None:
        assert OpenCodeAgent().config.activity_files == []

    def test_exposed_ports_empty(self) -> None:
        assert OpenCodeAgent().config.exposed_ports == []

    def test_default_base_image_is_none(self) -> None:
        assert OpenCodeAgent().config.default_base_image is None

    def test_secret_env_vars_anthropic_default(self) -> None:
        cfg = OpenCodeAgent().config
        assert "ANTHROPIC_API_KEY" in cfg.secret_env_vars

    def test_secret_env_vars_openai(self) -> None:
        cfg = OpenCodeAgent(provider="openai").config
        assert "OPENAI_API_KEY" in cfg.secret_env_vars

    def test_secret_env_vars_chatgpt_empty(self) -> None:
        cfg = OpenCodeAgent(provider="chatgpt").config
        assert cfg.secret_env_vars == []

    def test_secret_env_vars_vertex_empty(self) -> None:
        cfg = OpenCodeAgent(provider="vertex").config
        assert cfg.secret_env_vars == []

    def test_passthrough_vars_vertex(self) -> None:
        cfg = OpenCodeAgent(provider="vertex").config
        assert "ANTHROPIC_VERTEX_PROJECT_ID" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_PROJECT" in cfg.passthrough_env_vars
        assert "VERTEX_LOCATION" in cfg.passthrough_env_vars

    def test_passthrough_prefixes_vertex(self) -> None:
        cfg = OpenCodeAgent(provider="vertex").config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes

    def test_passthrough_vars_anthropic_empty(self) -> None:
        cfg = OpenCodeAgent().config
        assert cfg.passthrough_env_vars == []

    def test_passthrough_prefixes_anthropic_empty(self) -> None:
        cfg = OpenCodeAgent().config
        assert cfg.passthrough_env_prefixes == []


class TestOpenCodeAgentDockerfile:
    """Tests for OpenCodeAgent.dockerfile_install_lines."""

    def test_returns_list(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_contains_install_url(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "opencode.ai/install" in text

    def test_sets_path(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "/home/paude/.opencode/bin" in text

    def test_uses_container_home(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/custom/home")
        text = "\n".join(lines)
        assert "/custom/home" in text

    def test_pipefail_shell(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "pipefail" in text

    def test_binary_verification(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "test -x /home/paude/.opencode/bin/opencode" in text

    def test_shell_reset(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        assert 'SHELL ["/bin/sh", "-c"]' in lines

    def test_error_message(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "ERROR" in text
        assert "installation failed" in text

    def test_contains_umask(self) -> None:
        lines = OpenCodeAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "umask 0002" in text


class TestOpenCodeAgentLaunchCommand:
    """Tests for OpenCodeAgent.launch_command."""

    def test_no_args(self) -> None:
        assert OpenCodeAgent().launch_command("") == "opencode"

    def test_with_args(self) -> None:
        assert OpenCodeAgent().launch_command("--flag") == "opencode --flag"


class TestOpenCodeAgentHostConfigMounts:
    """Tests for OpenCodeAgent.host_config_mounts."""

    def test_empty_when_no_config(self, tmp_path: Path) -> None:
        mounts = OpenCodeAgent().host_config_mounts(tmp_path)
        assert mounts == []


class TestOpenCodeAgentBuildEnvironment:
    """Tests for OpenCodeAgent.build_environment."""

    def test_includes_static_env_vars_when_no_host_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = OpenCodeAgent().build_environment()
            assert env == {"OPENCODE_DISABLE_AUTOUPDATE": "true"}

    def test_does_not_include_secret_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = OpenCodeAgent().build_environment()
            assert "ANTHROPIC_API_KEY" not in env

    def test_secret_env_collects_anthropic_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = build_secret_environment_from_config(OpenCodeAgent().config)
            assert env == {"ANTHROPIC_API_KEY": "sk-test"}

    def test_secret_env_collects_openai_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = build_secret_environment_from_config(
                OpenCodeAgent(provider="openai").config
            )
            assert env == {"OPENAI_API_KEY": "sk-test"}

    def test_passes_through_vertex_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_VERTEX_PROJECT_ID": "proj-1", "UNRELATED": "x"},
            clear=True,
        ):
            env = OpenCodeAgent(provider="vertex").build_environment()
            assert "ANTHROPIC_VERTEX_PROJECT_ID" in env


class TestOpenCodeAgentSandboxConfig:
    """Tests for OpenCodeAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_creates_config_dirs(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert ".config/opencode" in script
        assert ".local/share/opencode" in script

    def test_contains_opencode_json(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "opencode.json" in script

    def test_contains_schema(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "opencode.ai/config.json" in script

    def test_contains_autoupdate_false(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"autoupdate": false' in script

    def test_contains_share_disabled(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"share": "disabled"' in script

    def test_home_path_parameterized(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.config/opencode" in script

    def test_workspace_path_parameterized(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config(
            "/home/paude", "/pvc/workspace", ""
        )
        assert "/pvc/workspace/opencode.json" in script

    def test_does_not_overwrite_existing_config(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '! -f "$config_file"' in script

    def test_yolo_sets_permission_allow_all(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"permission"' in script
        assert '"*": "allow"' in script

    def test_no_yolo_no_permission_block(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"permission"' not in script

    def test_anthropic_provider_config(self) -> None:
        script = OpenCodeAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "anthropic" in script
        assert "ANTHROPIC_API_KEY" in script

    def test_openai_provider_config(self) -> None:
        script = OpenCodeAgent(provider="openai").apply_sandbox_config(
            "/home/paude", "/workspace", ""
        )
        assert "openai" in script
        assert "OPENAI_API_KEY" in script

    def test_vertex_provider_config(self) -> None:
        script = OpenCodeAgent(provider="vertex").apply_sandbox_config(
            "/home/paude", "/workspace", ""
        )
        assert "google-vertex" in script
        assert '"project": "{env:GOOGLE_CLOUD_PROJECT}"' in script
        assert '"location": "{env:VERTEX_LOCATION}"' in script

    def test_chatgpt_sandbox_config_no_provider_block(self) -> None:
        script = OpenCodeAgent(provider="chatgpt").apply_sandbox_config(
            "/home/paude", "/workspace", ""
        )
        assert '"provider"' not in script


class TestOpenClawAgentConfig:
    """Tests for OpenClawAgent configuration values."""

    def test_name(self) -> None:
        assert OpenClawAgent().config.name == "openclaw"

    def test_display_name(self) -> None:
        assert OpenClawAgent().config.display_name == "OpenClaw"

    def test_process_name(self) -> None:
        assert OpenClawAgent().config.process_name == "node"

    def test_session_name(self) -> None:
        assert OpenClawAgent().config.session_name == "openclaw"

    def test_config_dir_name(self) -> None:
        assert OpenClawAgent().config.config_dir_name == ".openclaw"

    def test_config_file_name_is_none(self) -> None:
        assert OpenClawAgent().config.config_file_name is None

    def test_yolo_flag_is_none(self) -> None:
        assert OpenClawAgent().config.yolo_flag is None

    def test_clear_command_is_none(self) -> None:
        assert OpenClawAgent().config.clear_command is None

    def test_passthrough_vars(self) -> None:
        cfg = OpenClawAgent().config
        assert "ANTHROPIC_VERTEX_PROJECT_ID" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_PROJECT" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_PROJECT_ID" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_LOCATION" in cfg.passthrough_env_vars
        assert "CLOUD_ML_REGION" in cfg.passthrough_env_vars

    def test_passthrough_prefixes(self) -> None:
        cfg = OpenClawAgent().config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes

    def test_secret_env_vars(self) -> None:
        # Default provider is vertex, which has no secret env vars
        cfg = OpenClawAgent().config
        assert cfg.secret_env_vars == []

    def test_secret_env_vars_openai_provider(self) -> None:
        cfg = OpenClawAgent(provider="openai").config
        assert "OPENAI_API_KEY" in cfg.secret_env_vars

    def test_secret_env_vars_anthropic_provider(self) -> None:
        cfg = OpenClawAgent(provider="anthropic").config
        assert "ANTHROPIC_API_KEY" in cfg.secret_env_vars

    def test_extra_domain_aliases(self) -> None:
        assert OpenClawAgent().config.extra_domain_aliases == ["openclaw"]

    def test_exposed_ports(self) -> None:
        cfg = OpenClawAgent().config
        assert cfg.exposed_ports == [(18789, 18789)]

    def test_default_base_image(self) -> None:
        cfg = OpenClawAgent().config
        assert cfg.default_base_image == "ghcr.io/openclaw/openclaw:latest"

    def test_env_vars_node_proxy(self) -> None:
        assert OpenClawAgent().config.env_vars == {"NODE_USE_ENV_PROXY": "1"}


class TestOpenClawAgentDockerfile:
    """Tests for OpenClawAgent.dockerfile_install_lines."""

    def test_returns_list(self) -> None:
        lines = OpenClawAgent().dockerfile_install_lines("/home/paude")
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_contains_openclaw(self) -> None:
        lines = OpenClawAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "openclaw" in text.lower()

    def test_contains_node_proxy_env(self) -> None:
        lines = OpenClawAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        assert "NODE_USE_ENV_PROXY=1" in text

    def test_handles_both_base_images(self) -> None:
        lines = OpenClawAgent().dockerfile_install_lines("/home/paude")
        text = "\n".join(lines)
        # Should handle case where openclaw is already installed
        assert "already installed" in text.lower() or "openclaw" in text.lower()


class TestOpenClawAgentLaunchCommand:
    """Tests for OpenClawAgent.launch_command."""

    def test_no_args(self) -> None:
        cmd = OpenClawAgent().launch_command("")
        assert "openclaw" in cmd
        assert "gateway" in cmd

    def test_with_args(self) -> None:
        cmd = OpenClawAgent().launch_command("--verbose")
        assert "openclaw" in cmd
        assert "--verbose" in cmd


class TestOpenClawAgentHostConfigMounts:
    """Tests for OpenClawAgent.host_config_mounts."""

    def test_empty_when_no_config(self, tmp_path: Path) -> None:
        mounts = OpenClawAgent().host_config_mounts(tmp_path)
        assert mounts == []

    def test_mounts_openclaw_dir(self, tmp_path: Path) -> None:
        openclaw_dir = tmp_path / ".openclaw"
        openclaw_dir.mkdir()
        mounts = OpenClawAgent().host_config_mounts(tmp_path)
        assert mounts == []


class TestOpenClawAgentBuildEnvironment:
    """Tests for OpenClawAgent.build_environment."""

    def test_includes_static_env_vars_when_no_host_vars_set(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = OpenClawAgent().build_environment()
            assert env == {"NODE_USE_ENV_PROXY": "1"}

    def test_does_not_include_secret_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_API_KEY": "sk-test", "UNRELATED": "x"},
            clear=True,
        ):
            env = OpenClawAgent().build_environment()
            assert "ANTHROPIC_API_KEY" not in env

    def test_secret_env_collects_api_keys(self) -> None:
        # Default provider (vertex) has no secret env vars
        with patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "sk-ant",
                "OPENAI_API_KEY": "sk-oai",
                "UNRELATED": "x",
            },
            clear=True,
        ):
            env = build_secret_environment_from_config(OpenClawAgent().config)
            assert env == {}

    def test_secret_env_collects_openai_key(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "sk-oai", "UNRELATED": "x"},
            clear=True,
        ):
            env = build_secret_environment_from_config(
                OpenClawAgent(provider="openai").config
            )
            assert env == {"OPENAI_API_KEY": "sk-oai"}


class TestOpenClawAgentSandboxConfig:
    """Tests for OpenClawAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_contains_port_config(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "18789" in script

    def test_contains_workspace(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/pvc/workspace", ""
        )
        assert "/pvc/workspace" in script

    def test_home_path_parameterized(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.openclaw" in script

    def test_otel_block_conditional_on_env(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" in script

    def test_otel_uses_node_for_json_merge(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "node -e" in script

    def test_otel_enables_diagnostics_plugin(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "diagnostics-otel" in script

    def test_otel_configures_diagnostics_section(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "cfg.diagnostics.otel" in script

    def test_default_exec_host_gateway(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"host": "gateway"' in script

    def test_default_exec_security_allowlist(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"security": "allowlist"' in script

    def test_default_exec_ask_on_miss(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"ask": "on-miss"' in script

    def test_default_exec_strict_inline_eval(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"strictInlineEval": true' in script

    def test_default_fs_workspace_only(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"workspaceOnly": true' in script

    def test_default_elevated_disabled(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"enabled": false' in script

    def test_yolo_exec_host_gateway(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"host": "gateway"' in script

    def test_yolo_exec_security_full(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"security": "full"' in script

    def test_yolo_exec_ask_off(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"ask": "off"' in script

    def test_yolo_no_strict_inline_eval(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"strictInlineEval"' not in script

    def test_yolo_fs_still_workspace_only(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"workspaceOnly": true' in script

    def test_yolo_elevated_still_disabled(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"enabled": false' in script

    def test_default_tools_profile_coding(self) -> None:
        script = OpenClawAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert '"profile": "coding"' in script

    def test_yolo_tools_profile_coding(self) -> None:
        script = OpenClawAgent().apply_sandbox_config(
            "/home/paude", "/workspace", "", yolo=True
        )
        assert '"profile": "coding"' in script
