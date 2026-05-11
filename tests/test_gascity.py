"""Tests for the Gas City multi-agent orchestration agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
        assert GascityAgent().config.config_dir_name == ".gascity"

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
            "NODE_USE_ENV_PROXY": "1",
            "BD_DOLT_AUTO_COMMIT": "off",
            "BD_EXPORT_AUTO": "false",
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
        assert "gascity" in cfg.extra_domain_aliases
        assert "claude" in cfg.extra_domain_aliases
        assert "gemini" in cfg.extra_domain_aliases
        assert "nodejs" in cfg.extra_domain_aliases

    def test_exposed_ports_empty(self) -> None:
        assert GascityAgent().config.exposed_ports == []

    def test_default_base_image_is_none(self) -> None:
        assert GascityAgent().config.default_base_image is None

    def test_activity_files_empty(self) -> None:
        assert GascityAgent().config.activity_files == []

    def test_install_script_is_noop(self) -> None:
        cfg = GascityAgent().config
        assert "pre-installed" in cfg.install_script


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

    def test_contains_gemini_cli(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "@google/gemini-cli" in text

    def test_contains_gemini_otel_patch(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "patch-gemini-otel-proxy.sh" in text

    def test_contains_claude_install(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "claude.ai/install.sh" in text

    def test_contains_claude_binary_check(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "test -x" in text
        assert "claude" in text

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

    def test_dolt_config_dir_group_writable(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "chmod -R g+rwX /home/paude/.dolt" in text

    def test_arch_detection(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "uname -m" in text
        assert "amd64" in text
        assert "arm64" in text

    def test_pipefail_shell(self) -> None:
        text = "\n".join(GascityAgent().dockerfile_install_lines("/home/paude"))
        assert "pipefail" in text

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
                "NODE_USE_ENV_PROXY": "1",
                "BD_DOLT_AUTO_COMMIT": "off",
                "BD_EXPORT_AUTO": "false",
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

    def test_contains_claude_trust(self) -> None:
        script = GascityAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "hasCompletedOnboarding" in script
        assert "hasTrustDialogAccepted" in script

    def test_contains_gemini_trust(self) -> None:
        script = GascityAgent().apply_sandbox_config("/home/paude", "/workspace", "")
        assert "trustedFolders.json" in script
        assert "TRUST_FOLDER" in script

    def test_contains_workspace(self) -> None:
        script = GascityAgent().apply_sandbox_config(
            "/home/paude", "/pvc/workspace", ""
        )
        assert "/pvc/workspace" in script

    def test_home_path_parameterized(self) -> None:
        script = GascityAgent().apply_sandbox_config("/custom/home", "/workspace", "")
        assert "/custom/home/.claude.json" in script
        assert "/custom/home/.gemini" in script
