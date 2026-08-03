"""Tests for the Gas City multi-agent orchestration agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from paude.agents import get_agents
from paude.agents.base import compose_dockerfile_install_lines
from paude.agents.gascity import BD_VERSION, DOLT_VERSION, GC_VERSION, GascityAgent


class TestGascityAgentConfig:
    """Tests for GascityAgent configuration values."""

    def test_name(self) -> None:
        assert GascityAgent().config.name == "gascity"

    def test_display_name(self) -> None:
        assert GascityAgent().config.display_name == "Gas City"

    def test_process_name(self) -> None:
        assert GascityAgent().config.process_name == "gc"

    def test_session_name(self) -> None:
        assert GascityAgent().config.session_name == "gascity"

    def test_config_dir_name(self) -> None:
        assert GascityAgent().config.config_dir_name == ".gc"

    def test_config_file_name_is_none(self) -> None:
        assert GascityAgent().config.config_file_name is None

    def test_yolo_flag_is_none(self) -> None:
        assert GascityAgent().config.yolo_flag is None

    def test_clear_command_is_none(self) -> None:
        assert GascityAgent().config.clear_command is None

    def test_env_vars(self) -> None:
        cfg = GascityAgent().config
        assert cfg.env_vars == {
            "CLAUDE_CODE_USE_VERTEX": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "paude-proxy-managed",
            "NODE_USE_ENV_PROXY": "1",
            "BD_DOLT_AUTO_COMMIT": "off",
            "BD_EXPORT_AUTO": "false",
            "DO_NOT_TRACK": "1",
            "GC_DISABLE_USAGE_METRICS": "1",
        }

    def test_passthrough_vars(self) -> None:
        cfg = GascityAgent().config
        assert "ANTHROPIC_VERTEX_PROJECT_ID" in cfg.passthrough_env_vars
        assert "GOOGLE_CLOUD_PROJECT" in cfg.passthrough_env_vars

    def test_passthrough_prefixes(self) -> None:
        cfg = GascityAgent().config
        assert "CLOUDSDK_AUTH_" in cfg.passthrough_env_prefixes

    def test_extra_domain_aliases(self) -> None:
        cfg = GascityAgent().config
        # Child-agent domains are contributed by the resolved composition.
        assert cfg.extra_domain_aliases == []

    def test_exposed_ports_empty(self) -> None:
        assert GascityAgent().config.exposed_ports == []

    def test_default_base_image_is_none(self) -> None:
        assert GascityAgent().config.default_base_image is None

    def test_activity_files_empty(self) -> None:
        assert GascityAgent().config.activity_files == []

    def test_install_script_is_noop(self) -> None:
        cfg = GascityAgent().config
        assert "pre-installed" in cfg.install_script

    def test_bundled_agents(self) -> None:
        assert GascityAgent().config.bundled_agents == ["claude", "gemini"]


class TestGascityAgentDockerfile:
    """Tests for GascityAgent.dockerfile_install_lines."""

    def test_returns_list(self) -> None:
        lines = GascityAgent().dockerfile_install_lines("/home/paude")
        assert isinstance(lines, list)
        assert len(lines) > 0

    def test_contains_nodejs(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "nodejs" in text

    def test_contains_npm(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "npm" in text

    def test_core_excludes_gemini_cli(self) -> None:
        # Gemini install moved out to the bundled GeminiAgent.
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "@google/gemini-cli" not in text

    def test_core_excludes_claude_install(self) -> None:
        # Claude Code install moved out to the bundled ClaudeAgent.
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "claude.ai/install.sh" not in text

    def test_contains_dolt(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "dolthub/dolt" in text
        assert DOLT_VERSION in text

    def test_contains_bd(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "gastownhall/beads" in text
        assert BD_VERSION in text

    def test_contains_gc(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "gastownhall/gascity" in text
        assert GC_VERSION in text

    def test_uses_pinned_release_urls(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert f"/dolt/releases/download/v{DOLT_VERSION}" in text
        assert f"/beads/releases/download/v{BD_VERSION}" in text
        assert f"/gascity/releases/download/v{GC_VERSION}" in text
        assert "releases/latest" not in text

    def test_contains_flock(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "util-linux" in text

    def test_contains_lsof(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "lsof" in text

    def test_disables_dolt_metrics(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "metrics.disabled" in text
        assert "dolt config --global --set" in text

    def test_sets_dolt_author_identity(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "user.name" in text
        assert "user.email" in text

    def test_dolt_config_dir_group_writable(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "chmod -R g+rwX /home/paude/.dolt" in text

    def test_arch_detection(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "uname -m" in text
        assert "amd64" in text
        assert "arm64" in text

    def test_sets_path(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "/home/paude/.local/bin" in text

    def test_uses_container_home(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/custom/home"))
        assert "/custom/home" in text


class TestGascityAgentLaunchCommand:
    """Tests for GascityAgent.launch_command."""

    def test_no_args(self) -> None:
        assert GascityAgent().launch_command("") == "bash"

    def test_with_args(self) -> None:
        assert GascityAgent().launch_command("--foo") == "bash"


class TestGascityAgentHostConfigMounts:
    """Tests for GascityAgent.host_config_mounts."""

    def test_empty(self, tmp_path: Path) -> None:
        mounts = GascityAgent().host_config_mounts(tmp_path)
        assert mounts == []


class TestGascityAgentBuildEnvironment:
    """Tests for GascityAgent.build_environment."""

    def test_includes_static_env_vars(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            env = GascityAgent().build_environment()
            assert env == {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "CLAUDE_CODE_OAUTH_TOKEN": "paude-proxy-managed",
                "NODE_USE_ENV_PROXY": "1",
                "BD_DOLT_AUTO_COMMIT": "off",
                "BD_EXPORT_AUTO": "false",
                "DO_NOT_TRACK": "1",
                "GC_DISABLE_USAGE_METRICS": "1",
            }

    def test_passes_through_vertex_vars(self) -> None:
        with patch.dict(
            "os.environ",
            {"ANTHROPIC_VERTEX_PROJECT_ID": "proj-1", "UNRELATED": "x"},
            clear=True,
        ):
            env = GascityAgent().build_environment()
            assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "proj-1"
            assert env["CLAUDE_CODE_USE_VERTEX"] == "1"

    def test_passes_through_prefix_vars(self) -> None:
        test_val = "abc"  # noqa: S105
        with patch.dict(
            "os.environ",
            {"CLOUDSDK_AUTH_TOKEN": test_val},
            clear=True,
        ):
            env = GascityAgent().build_environment()
            assert env["CLOUDSDK_AUTH_TOKEN"] == test_val


class TestGascityAgentSandboxConfig:
    """Tests for GascityAgent.apply_sandbox_config."""

    def test_returns_bash_script(self) -> None:
        script = GascityAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert script.startswith("#!/bin/bash")

    def test_core_only_contains_dolt_identity(self) -> None:
        script = GascityAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "dolt config --global --set user.name" in script
        assert "hasCompletedOnboarding" not in script
        assert "trustedFolders.json" not in script

    def test_contains_dolt_identity_from_gitconfig(self) -> None:
        script = GascityAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "git config --global user.name" in script
        assert "git config --global user.email" in script
        assert "dolt config --global --set user.name" in script
        assert "dolt config --global --set user.email" in script

    def test_composed_sandbox_includes_child_agent_configs(self) -> None:
        from paude.backends.session_env import generate_sandbox_config_script

        script = generate_sandbox_config_script(
            get_agents(["gascity"]), "/pvc/workspace", ""
        )
        assert "/pvc/workspace" in script
        assert "hasCompletedOnboarding" in script
        assert "trustedFolders.json" in script
        assert "/home/paude/.claude.json" in script
        assert "/home/paude/.gemini" in script


class TestGascityComposedInstall:
    """The composed --agent gascity install must still equal today's toolchain.

    gascity's own Dockerfile lines now cover only the Gas City core (gc/dolt/bd
    + Node prereq); Claude Code and Gemini CLI are contributed by the bundled
    agents and stitched back in by compose_dockerfile_install_lines(). These
    tests pin the composed result so the install set stays identical to the
    pre-refactor hardcoded lines.
    """

    def _composed(self, home: str = "/home/paude") -> str:
        composition = get_agents(["gascity"])
        return "\n".join(compose_dockerfile_install_lines(composition.agents, home))

    def test_install_set_is_gascity_claude_gemini(self) -> None:
        assert get_agents(["gascity"]).names == ["gascity", "claude", "gemini"]

    def test_explicit_install_set_excludes_implicit_gemini(self) -> None:
        composition = get_agents(["gascity", "claude", "codex"], include_bundled=False)
        assert composition.names == ["gascity", "claude", "codex"]

    def test_primary_is_gascity(self) -> None:
        assert get_agents(["gascity"]).primary.config.name == "gascity"

    def test_contains_gc_dolt_bd(self) -> None:
        text = self._composed()
        assert "gastownhall/gascity" in text
        assert GC_VERSION in text
        assert "dolthub/dolt" in text
        assert DOLT_VERSION in text
        assert "gastownhall/beads" in text
        assert BD_VERSION in text

    def test_contains_claude(self) -> None:
        text = self._composed()
        assert "claude.ai/install.sh" in text
        assert "pipefail" in text  # from Claude's verified install

    def test_contains_gemini(self) -> None:
        text = self._composed()
        assert "@google/gemini-cli" in text
        assert "patch-gemini-otel-proxy.sh" in text

    def test_contains_node_and_flock(self) -> None:
        text = self._composed()
        assert "nodejs" in text
        assert "util-linux" in text
        assert "lsof" in text

    def test_node_prereq_installed_once(self) -> None:
        # gascity core and the bundled Gemini agent both need Node.js; the
        # composer must collapse the shared package install to a single layer.
        lines = compose_dockerfile_install_lines(
            get_agents(["gascity"]).agents, "/home/paude"
        )
        node_installs = [
            line for line in lines if line.startswith("RUN if command -v node")
        ]
        assert len(node_installs) == 1

    def test_ends_with_canonical_layout(self) -> None:
        lines = compose_dockerfile_install_lines(
            get_agents(["gascity"]).agents, "/home/paude"
        )
        assert lines[-2:] == ["USER paude", "WORKDIR /home/paude"]


class TestGascityBuildPathInstall:
    """The real image build path for --agent gascity must install every toolchain.

    The composed install set (TestGascityComposedInstall) is only the correct
    image if the production Dockerfile generators actually route through
    get_agents() + compose_dockerfile_install_lines(). These tests pin the
    generators themselves — the artifact the product really builds — so a
    refactor that leaves the composer without production callers is caught:
    they fail if a bundled toolchain is dropped from the built gascity image.
    """

    def test_claude_layer_dockerfile_installs_bundled_toolchains(self) -> None:
        # generate_claude_layer_dockerfile is the path --agent gascity uses via
        # ImageManager._ensure_runtime_image.
        from paude.agents import get_agent
        from paude.config.claude_layer import generate_claude_layer_dockerfile

        dockerfile = generate_claude_layer_dockerfile(agent=get_agent("gascity"))
        assert "claude.ai/install.sh" in dockerfile  # Claude Code
        assert "@google/gemini-cli" in dockerfile  # Gemini CLI
        assert "gastownhall/gascity" in dockerfile  # Gas City core

    def test_workspace_dockerfile_installs_bundled_toolchains(self) -> None:
        from paude.agents import get_agent
        from paude.config.dockerfile import generate_workspace_dockerfile
        from paude.config.models import PaudeConfig

        dockerfile = generate_workspace_dockerfile(
            PaudeConfig(), agent=get_agent("gascity")
        )
        assert "claude.ai/install.sh" in dockerfile
        assert "@google/gemini-cli" in dockerfile
        assert "gastownhall/gascity" in dockerfile

    def test_pip_install_dockerfile_installs_bundled_toolchains(self) -> None:
        from paude.agents import get_agent
        from paude.config.dockerfile import generate_pip_install_dockerfile
        from paude.config.models import PaudeConfig

        dockerfile = generate_pip_install_dockerfile(
            PaudeConfig(), include_claude_install=True, agent=get_agent("gascity")
        )
        assert "claude.ai/install.sh" in dockerfile
        assert "@google/gemini-cli" in dockerfile
        assert "gastownhall/gascity" in dockerfile

    def test_claude_only_image_excludes_gemini(self) -> None:
        # Guard the negative: a single-agent image must not pull in bundled CLIs
        # it never requested, so the composer expansion stays scoped.
        from paude.agents import get_agent
        from paude.config.claude_layer import generate_claude_layer_dockerfile

        dockerfile = generate_claude_layer_dockerfile(agent=get_agent("claude"))
        assert "claude.ai/install.sh" in dockerfile
        assert "@google/gemini-cli" not in dockerfile

    def test_explicit_composition_image_excludes_gemini(self) -> None:
        from paude.config.claude_layer import generate_claude_layer_dockerfile

        composition = get_agents(["gascity", "claude", "codex"], include_bundled=False)
        dockerfile = generate_claude_layer_dockerfile(composition=composition)
        assert "gastownhall/gascity" in dockerfile
        assert "claude.ai/install.sh" in dockerfile
        assert "openai/codex" in dockerfile
        assert "@google/gemini-cli" not in dockerfile
        assert "/home/paude/.gc" in dockerfile
        assert "/home/paude/.claude" in dockerfile
        assert "/home/paude/.codex" in dockerfile
