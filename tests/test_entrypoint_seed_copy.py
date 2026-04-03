"""Tests for entrypoint-session.sh config logic (Podman backend).

These tests exercise the bash config blocks (seed copy, persist, sandbox)
by extracting them into minimal scripts, running them in a temporary
directory, and verifying results.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

# Paths to the real entrypoint and library files, used by contract tests
ENTRYPOINT_PATH = (
    Path(__file__).parent.parent / "containers" / "paude" / "entrypoint-session.sh"
)
ENTRYPOINT_LIB_CONFIG_PATH = (
    Path(__file__).parent.parent / "containers" / "paude" / "entrypoint-lib-config.sh"
)
ENTRYPOINT_LIB_CREDENTIALS_PATH = (
    Path(__file__).parent.parent
    / "containers"
    / "paude"
    / "entrypoint-lib-credentials.sh"
)
ENTRYPOINT_LIB_INSTALL_PATH = (
    Path(__file__).parent.parent / "containers" / "paude" / "entrypoint-lib-install.sh"
)


def _read_all_entrypoint_files() -> str:
    """Read the main entrypoint and all library files concatenated."""
    return (
        ENTRYPOINT_PATH.read_text()
        + ENTRYPOINT_LIB_CONFIG_PATH.read_text()
        + ENTRYPOINT_LIB_CREDENTIALS_PATH.read_text()
        + ENTRYPOINT_LIB_INSTALL_PATH.read_text()
    )


def _run_script(script: str) -> subprocess.CompletedProcess[str]:
    """Run a bash script and return the result."""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestEntrypointContract:
    """Contract tests verifying entrypoint-session.sh contains the fix.

    These prevent drift between the test reimplementation and the real script.
    If the entrypoint is reverted, these tests catch it.
    """

    def test_entrypoint_sources_sandbox_config_script(self) -> None:
        """The entrypoint must source the Python-generated sandbox config script."""
        content = ENTRYPOINT_PATH.read_text()
        assert "agent-sandbox-config.sh" in content, (
            "entrypoint-session.sh must source agent-sandbox-config.sh"
        )
        assert "PAUDE_SUPPRESS_PROMPTS" in content, (
            "entrypoint-session.sh must check PAUDE_SUPPRESS_PROMPTS before sourcing"
        )

    def test_entrypoint_checks_tmux_before_sandbox_config(self) -> None:
        """tmux has-session check must appear before sandbox config sourcing."""
        content = ENTRYPOINT_PATH.read_text()
        tmux_check_pos = content.find("tmux -u has-session")
        sandbox_source_pos = content.find("agent-sandbox-config.sh")
        assert tmux_check_pos != -1
        assert sandbox_source_pos != -1
        assert tmux_check_pos < sandbox_source_pos, (
            "tmux session check must come before sandbox config sourcing"
        )

    def test_entrypoint_cp_does_not_preserve_selinux(self) -> None:
        """Copy commands must not use cp -a (which preserves SELinux xattr)."""
        import re

        content = _read_all_entrypoint_files()
        # cp -a preserves xattr including security.selinux — must not be used
        # for cross-filesystem copies (image → PVC, credentials → PVC)
        cp_a_lines = re.findall(r"cp -a .*\$.*DIR", content)
        assert len(cp_a_lines) == 0, (
            f"entrypoint files must not use 'cp -a' for config copies "
            f"(preserves SELinux xattr): {cp_a_lines}"
        )

    def test_entrypoint_has_selinux_remediation(self) -> None:
        """persist_agent_config must fix SELinux context with chcon."""
        content = ENTRYPOINT_LIB_CONFIG_PATH.read_text()
        assert "chcon" in content, (
            "entrypoint-lib-config.sh must include chcon for SELinux remediation"
        )
        assert "--reference=/pvc" in content, (
            "chcon must use --reference=/pvc to inherit PVC SELinux context"
        )


def _build_gemini_sandbox_script(
    home_dir: str,
    workspace: str,
    suppress_prompts: bool,
) -> str:
    """Build a script using Python-generated Gemini sandbox config."""
    if not suppress_prompts:
        return f'#!/bin/bash\nexport HOME="{home_dir}"\n'

    from paude.agents.gemini import GeminiAgent

    agent = GeminiAgent()
    config_script = agent.apply_sandbox_config(home_dir, workspace, "")
    return f'#!/bin/bash\nset -e\nexport HOME="{home_dir}"\n{config_script}'


def _build_sandbox_script(
    home_dir: str,
    workspace: str,
    suppress_prompts: bool,
    claude_args: str = "",
    *,
    yolo: bool = False,
) -> str:
    """Build a script using Python-generated Claude sandbox config."""
    if not suppress_prompts:
        return f'#!/bin/bash\nexport HOME="{home_dir}"\n'

    from paude.agents.claude import ClaudeAgent

    agent = ClaudeAgent()
    config_script = agent.apply_sandbox_config(
        home_dir, workspace, claude_args, yolo=yolo
    )

    env_lines = f'export HOME="{home_dir}"\n'
    return f"#!/bin/bash\nset -e\n{env_lines}{config_script}"


class TestSandboxPromptSuppression:
    """Tests for apply_sandbox_config() in entrypoint-session.sh."""

    def test_creates_trust_config_when_suppress_enabled(self, tmp_path: Path) -> None:
        """Trust + onboarding set when PAUDE_SUPPRESS_PROMPTS=1 (new file)."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        claude_json = json.loads((home / ".claude.json").read_text())
        assert claude_json["hasCompletedOnboarding"] is True
        assert claude_json["projects"][workspace]["hasTrustDialogAccepted"] is True

    def test_merges_into_existing_claude_json(self, tmp_path: Path) -> None:
        """Merged into existing ~/.claude.json preserving other keys."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        existing = {"existingKey": "preserved", "numericField": 42}
        (home / ".claude.json").write_text(json.dumps(existing))

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        claude_json = json.loads((home / ".claude.json").read_text())
        assert claude_json["existingKey"] == "preserved"
        assert claude_json["numericField"] == 42
        assert claude_json["hasCompletedOnboarding"] is True
        assert claude_json["projects"][workspace]["hasTrustDialogAccepted"] is True

    def test_patches_settings_json_with_skip_permissions(self, tmp_path: Path) -> None:
        """settings.json patched when PAUDE_SUPPRESS_PROMPTS=1 + skip perms."""
        home = tmp_path / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        workspace = "/pvc/workspace"

        script = _build_sandbox_script(
            str(home),
            workspace,
            suppress_prompts=True,
            yolo=True,
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        settings = json.loads((home / ".claude" / "settings.json").read_text())
        assert settings["skipDangerousModePermissionPrompt"] is True

    def test_merges_settings_json_preserving_existing(self, tmp_path: Path) -> None:
        """Existing settings.json keys are preserved during merge."""
        home = tmp_path / "home"
        home.mkdir()
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        workspace = "/pvc/workspace"

        existing = {"permissions": {"allow": ["Bash"]}}
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        script = _build_sandbox_script(
            str(home),
            workspace,
            suppress_prompts=True,
            yolo=True,
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        settings = json.loads((claude_dir / "settings.json").read_text())
        assert settings["skipDangerousModePermissionPrompt"] is True
        assert settings["permissions"]["allow"] == ["Bash"]

    def test_no_changes_when_suppress_unset(self, tmp_path: Path) -> None:
        """No changes when PAUDE_SUPPRESS_PROMPTS is unset."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=False)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert not (home / ".claude.json").exists()
        assert not (home / ".claude").exists()

    def test_no_settings_json_without_yolo(self, tmp_path: Path) -> None:
        """No settings.json changes when yolo mode is not enabled."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # claude.json should exist (trust config)
        assert (home / ".claude.json").exists()
        # settings.json should NOT exist
        assert not (home / ".claude" / "settings.json").exists()


class TestGeminiSandboxConfig:
    """Tests for Gemini apply_sandbox_config() in entrypoint-session.sh."""

    def test_creates_trusted_folders_json(self, tmp_path: Path) -> None:
        """trustedFolders.json created with workspace trust when suppress enabled."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        script = _build_gemini_sandbox_script(
            str(home), workspace, suppress_prompts=True
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        trusted = json.loads((home / ".gemini" / "trustedFolders.json").read_text())
        assert trusted[workspace] == "TRUST_FOLDER"

    def test_merges_into_existing_trusted_folders(self, tmp_path: Path) -> None:
        """Existing trusted folders are preserved when adding workspace."""
        home = tmp_path / "home"
        home.mkdir()
        gemini_dir = home / ".gemini"
        gemini_dir.mkdir()
        workspace = "/pvc/workspace"

        existing = {"/other/project": "TRUST_FOLDER"}
        (gemini_dir / "trustedFolders.json").write_text(json.dumps(existing))

        script = _build_gemini_sandbox_script(
            str(home), workspace, suppress_prompts=True
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        trusted = json.loads((gemini_dir / "trustedFolders.json").read_text())
        assert trusted[workspace] == "TRUST_FOLDER"
        assert trusted["/other/project"] == "TRUST_FOLDER"

    def test_no_changes_when_suppress_unset(self, tmp_path: Path) -> None:
        """No changes when PAUDE_SUPPRESS_PROMPTS is unset."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        script = _build_gemini_sandbox_script(
            str(home), workspace, suppress_prompts=False
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert not (home / ".gemini").exists()

    def test_gemini_python_generates_trust_config(self) -> None:
        """Contract: Gemini agent's apply_sandbox_config handles trusted folders."""
        from paude.agents.gemini import GeminiAgent

        agent = GeminiAgent()
        script = agent.apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert "trustedFolders.json" in script, (
            "Gemini apply_sandbox_config must handle trustedFolders.json"
        )
        assert "TRUST_FOLDER" in script, (
            "Gemini apply_sandbox_config must set TRUST_FOLDER"
        )


class TestProjectRewriting:
    """Tests for project entry creation in container workspace."""

    def test_creates_fresh_project_entry(self, tmp_path: Path) -> None:
        """Fresh project entry with just hasTrustDialogAccepted for container ws."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        existing = {
            "hasCompletedOnboarding": True,
            "projects": {
                "/Volumes/SourceCode/paude": {
                    "hasTrustDialogAccepted": True,
                    "allowedTools": ["Bash", "Read"],
                }
            },
        }
        (home / ".claude.json").write_text(json.dumps(existing))

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        claude_json = json.loads((home / ".claude.json").read_text())
        assert list(claude_json["projects"].keys()) == [workspace]
        assert claude_json["projects"][workspace] == {
            "hasTrustDialogAccepted": True,
        }

    def test_discards_host_project_entries(self, tmp_path: Path) -> None:
        """Host project entries are not carried over."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        existing = {
            "projects": {
                "/Volumes/SourceCode/paude": {"hasTrustDialogAccepted": True},
                "/other/project": {"hasTrustDialogAccepted": True},
            }
        }
        (home / ".claude.json").write_text(json.dumps(existing))

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        claude_json = json.loads((home / ".claude.json").read_text())
        assert list(claude_json["projects"].keys()) == [workspace]

    def test_preserves_root_level_keys(self, tmp_path: Path) -> None:
        """Top-level .claude.json keys are preserved."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        existing = {
            "customKey": "preserved",
            "numericField": 42,
            "projects": {"/host/path": {"hasTrustDialogAccepted": True}},
        }
        (home / ".claude.json").write_text(json.dumps(existing))

        script = _build_sandbox_script(str(home), workspace, suppress_prompts=True)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        claude_json = json.loads((home / ".claude.json").read_text())
        assert claude_json["customKey"] == "preserved"
        assert claude_json["numericField"] == 42


class TestTerminalEnvBeforeTmux:
    """Regression: TERM/SHELL/LANG/LC_ALL must be exported before any tmux call.

    OpenShift runs containers with arbitrary UIDs whose default SHELL is
    /sbin/nologin. If tmux inherits that, `tmux new-session -d "bash -l"`
    uses nologin as default-shell, the session immediately exits, and the
    server dies with "no server running".
    """

    def _read_entrypoint(self) -> str:
        return ENTRYPOINT_PATH.read_text()

    def _first_tmux_command_pos(self, content: str) -> int:
        """Find the position of the first non-comment tmux invocation."""
        for line in content.split("\n"):
            stripped = line.strip()
            if "tmux " in stripped and not stripped.startswith("#"):
                # Return the position in the original content
                return content.find(stripped)
        return -1

    def test_shell_exported_before_first_tmux(self) -> None:
        """SHELL=/bin/bash must appear before any tmux invocation."""
        content = self._read_entrypoint()
        shell_pos = content.find("export SHELL=/bin/bash")
        first_tmux = self._first_tmux_command_pos(content)
        assert shell_pos != -1, "entrypoint-session.sh must export SHELL=/bin/bash"
        assert first_tmux != -1, "entrypoint-session.sh must contain tmux commands"
        assert shell_pos < first_tmux, (
            "export SHELL=/bin/bash must appear before the first tmux call. "
            "OpenShift arbitrary UIDs default SHELL to /sbin/nologin, which "
            "causes tmux to fail on session creation."
        )

    def test_term_exported_before_first_tmux(self) -> None:
        """TERM=xterm-256color must appear before any tmux invocation."""
        content = self._read_entrypoint()
        term_pos = content.find("export TERM=xterm-256color")
        first_tmux = self._first_tmux_command_pos(content)
        assert term_pos != -1, "entrypoint-session.sh must export TERM=xterm-256color"
        assert first_tmux != -1, "entrypoint-session.sh must contain tmux commands"
        assert term_pos < first_tmux, (
            "export TERM must appear before the first tmux call "
            "for correct color handling."
        )


# ---------------------------------------------------------------------------
# Helper for persist_agent_config tests
# ---------------------------------------------------------------------------


def _persist_bash_function(pvc_dir: str) -> str:
    """Return the persist_agent_config() bash function body for test scripts."""
    return textwrap.dedent(f"""\
        persist_agent_config() {{
            if [[ ! -d "{pvc_dir}" ]]; then
                return 0
            fi

            local pvc_config_dir="{pvc_dir}/$AGENT_CONFIG_DIR"
            local home_config_dir="$HOME/$AGENT_CONFIG_DIR"

            mkdir -p "$pvc_config_dir" 2>/dev/null || true
            chmod g+rwX "$pvc_config_dir" 2>/dev/null || true
            chcon -R --reference="{pvc_dir}" "$pvc_config_dir" 2>/dev/null || true

            if [[ -d "$home_config_dir" ]] && [[ ! -L "$home_config_dir" ]]; then
                cp -Rp "$home_config_dir/." "$pvc_config_dir/" 2>/dev/null || true
                rm -rf "$home_config_dir"
            fi

            if [[ ! -L "$home_config_dir" ]]; then
                rm -rf "$home_config_dir" 2>/dev/null || true
                ln -sf "$pvc_config_dir" "$home_config_dir"
            fi

            if [[ -n "$AGENT_CONFIG_FILE" ]]; then
                local pvc_config_file="{pvc_dir}/$AGENT_CONFIG_FILE"
                local home_config_file="$HOME/$AGENT_CONFIG_FILE"

                if [[ -f "$home_config_file" ]] && [[ ! -L "$home_config_file" ]]; then
                    if [[ ! -f "$pvc_config_file" ]]; then
                        cp -Rp "$home_config_file" "$pvc_config_file" 2>/dev/null || true
                    fi
                    rm -f "$home_config_file"
                fi

                if [[ ! -f "$pvc_config_file" ]]; then
                    echo '{{}}' > "$pvc_config_file" 2>/dev/null || true
                fi
                chmod g+rw "$pvc_config_file" 2>/dev/null || true
                chcon --reference="{pvc_dir}" "$pvc_config_file" 2>/dev/null || true

                if [[ ! -L "$home_config_file" ]]; then
                    rm -f "$home_config_file" 2>/dev/null || true
                    ln -sf "$pvc_config_file" "$home_config_file"
                fi
            fi
        }}
    """)


def _build_persist_script(
    home_dir: str,
    pvc_dir: str,
    agent_config_dir: str = ".claude",
    agent_config_file: str = ".claude.json",
) -> str:
    """Build a script that exercises persist_agent_config()."""
    persist_fn = _persist_bash_function(pvc_dir)
    return textwrap.dedent(f"""\
        #!/bin/bash
        set -e
        export HOME="{home_dir}"
        AGENT_CONFIG_DIR="{agent_config_dir}"
        AGENT_CONFIG_FILE="{agent_config_file}"

        {persist_fn}
        persist_agent_config
    """)


class TestPersistAgentConfig:
    """Tests for persist_agent_config() — symlinks config to PVC."""

    def test_creates_symlinks_on_fresh_volume(self, tmp_path: Path) -> None:
        """First start: creates PVC dirs and symlinks from HOME."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        script = _build_persist_script(str(home), str(pvc))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Config dir is a symlink to PVC
        config_dir = home / ".claude"
        assert config_dir.is_symlink()
        assert config_dir.resolve() == (pvc / ".claude").resolve()

        # Config file is a symlink to PVC
        config_file = home / ".claude.json"
        assert config_file.is_symlink()
        assert config_file.resolve() == (pvc / ".claude.json").resolve()

        # PVC has the actual directory and file
        assert (pvc / ".claude").is_dir()
        assert (pvc / ".claude.json").is_file()
        assert json.loads((pvc / ".claude.json").read_text()) == {}

    def test_preserves_pvc_state_on_upgrade(self, tmp_path: Path) -> None:
        """Upgrade: PVC has existing session data, new container gets symlinks."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        # Simulate existing PVC state from previous container
        pvc_claude = pvc / ".claude"
        pvc_claude.mkdir()
        (pvc_claude / "settings.json").write_text('{"key": "value"}')
        projects = pvc_claude / "projects"
        projects.mkdir()
        (projects / "session1.json").write_text('{"conversation": "data"}')
        (pvc / ".claude.json").write_text('{"hasCompletedOnboarding": true}')

        script = _build_persist_script(str(home), str(pvc))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Symlinks created
        assert (home / ".claude").is_symlink()
        assert (home / ".claude.json").is_symlink()

        # PVC state is preserved and accessible through symlinks
        assert (home / ".claude" / "settings.json").read_text() == '{"key": "value"}'
        assert (
            home / ".claude" / "projects" / "session1.json"
        ).read_text() == '{"conversation": "data"}'
        assert (
            json.loads((home / ".claude.json").read_text())["hasCompletedOnboarding"]
            is True
        )

    def test_merges_image_baked_config_into_pvc(self, tmp_path: Path) -> None:
        """First start with image-baked config: merges into PVC then symlinks."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        # Simulate image-baked config in HOME (real directory, not symlink)
        baked_config = home / ".claude"
        baked_config.mkdir()
        (baked_config / "settings.json").write_text('{"baked": true}')

        script = _build_persist_script(str(home), str(pvc))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # HOME config is now a symlink
        assert (home / ".claude").is_symlink()
        # Baked content was merged into PVC
        assert (pvc / ".claude" / "settings.json").read_text() == '{"baked": true}'

    def test_idempotent_on_reconnect(self, tmp_path: Path) -> None:
        """Reconnect: symlinks already exist, no-op."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        # First run
        script = _build_persist_script(str(home), str(pvc))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Write data through the symlink
        (home / ".claude" / "history.jsonl").write_text("line1\n")

        # Second run (reconnect)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Symlinks still work, data preserved
        assert (home / ".claude").is_symlink()
        assert (home / ".claude" / "history.jsonl").read_text() == "line1\n"

    def test_no_config_file_agent(self, tmp_path: Path) -> None:
        """Agent without config file (e.g., Gemini): only dir symlinked."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        script = _build_persist_script(
            str(home),
            str(pvc),
            agent_config_dir=".gemini",
            agent_config_file="",
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Config dir is symlinked
        assert (home / ".gemini").is_symlink()
        assert (pvc / ".gemini").is_dir()
        # No config file symlink created
        assert not (home / ".gemini.json").exists()

    def test_pvc_config_file_not_overwritten_by_existing_home(
        self, tmp_path: Path
    ) -> None:
        """If PVC already has config file, HOME copy doesn't overwrite it."""
        home = tmp_path / "home"
        home.mkdir()
        pvc = tmp_path / "pvc"
        pvc.mkdir()

        # PVC has config file with session state
        (pvc / ".claude.json").write_text('{"pvc": "state"}')
        # HOME has a different version (from image)
        (home / ".claude.json").write_text('{"home": "version"}')

        script = _build_persist_script(str(home), str(pvc))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # PVC version is preserved (not overwritten)
        assert json.loads((pvc / ".claude.json").read_text()) == {"pvc": "state"}


class TestPersistAgentConfigContract:
    """Contract tests for persist_agent_config in the real entrypoint."""

    def test_entrypoint_has_persist_function(self) -> None:
        """entrypoint-lib-config.sh must define persist_agent_config()."""
        content = ENTRYPOINT_LIB_CONFIG_PATH.read_text()
        assert "persist_agent_config()" in content, (
            "entrypoint-lib-config.sh must define persist_agent_config()"
        )

    def test_setup_credentials_called_before_persist(self) -> None:
        """setup_credentials must run before persist_agent_config.

        This ordering ensures host config lands in a real ~/.claude dir first,
        then persist_agent_config merges it into /pvc/.claude (preserving
        existing runtime state like sessions/history) and creates the symlink.
        """
        content = ENTRYPOINT_PATH.read_text()
        persist_pos = content.find("\npersist_agent_config\n")
        setup_pos = content.find("\nsetup_credentials\n")
        assert persist_pos != -1, "persist_agent_config must be called"
        assert setup_pos != -1, "setup_credentials must be called"
        assert setup_pos < persist_pos, (
            "setup_credentials must be called before persist_agent_config "
            "so host config is merged into PVC without clobbering runtime state"
        )

    def test_sandbox_config_python_uses_cp_for_claude_json(self) -> None:
        """Claude agent's apply_sandbox_config must use cp+rm, not mv."""
        from paude.agents.claude import ClaudeAgent

        agent = ClaudeAgent()
        script = agent.apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert 'cp -f "${claude_json}.tmp" "$claude_json"' in script, (
            "Claude sandbox config must use 'cp -f' for .claude.json to preserve symlinks"
        )
        assert 'rm -f "${claude_json}.tmp"' in script, (
            "Claude sandbox config must remove temp file after cp"
        )


class TestCursorSandboxConfig:
    """Tests for Cursor agent sandbox config generation and execution."""

    def test_cursor_sandbox_creates_workspace_trust(self, tmp_path: Path) -> None:
        """Cursor sandbox config must create .workspace-trusted file."""
        home = tmp_path / "home"
        home.mkdir()
        workspace = "/pvc/workspace"

        from paude.agents.cursor import CursorAgent

        agent = CursorAgent()
        config_script = agent.apply_sandbox_config(str(home), workspace, "")
        script = f'#!/bin/bash\nset -e\nexport HOME="{home}"\n{config_script}'

        result = _run_script(script)
        assert result.returncode == 0, (
            f"Cursor sandbox config script failed:\n{result.stderr}"
        )

        # Verify .workspace-trusted was created with correct content
        # workspace /pvc/workspace → slug pvc-workspace
        trusted_dir = home / ".cursor" / "projects" / "pvc-workspace"
        trusted_file = trusted_dir / ".workspace-trusted"
        assert trusted_file.exists(), (
            f".workspace-trusted not found; home contents: "
            f"{list((home / '.cursor').rglob('*')) if (home / '.cursor').exists() else 'no .cursor'}"
        )
        content = json.loads(trusted_file.read_text())
        assert content["workspacePath"] == workspace

    def test_cursor_python_generates_trust_config(self) -> None:
        """Contract: Cursor agent's apply_sandbox_config handles workspace trust."""
        from paude.agents.cursor import CursorAgent

        agent = CursorAgent()
        script = agent.apply_sandbox_config("/home/paude", "/pvc/workspace", "")
        assert "cli-config.json" in script, (
            "Cursor apply_sandbox_config must handle cli-config.json"
        )
        assert "workspace-trusted" in script, (
            "Cursor apply_sandbox_config must create workspace-trusted"
        )


class TestSetupCaTrustContract:
    """Contract tests: setup_ca_trust supports multiple distros."""

    def test_find_sys_ca_bundle_checks_multiple_paths(self) -> None:
        """_find_sys_ca_bundle must probe Debian, Alpine, and SUSE paths."""
        content = ENTRYPOINT_LIB_CREDENTIALS_PATH.read_text()
        assert "/etc/ssl/certs/ca-certificates.crt" in content, (
            "setup_ca_trust must support Debian/Ubuntu CA bundle path"
        )
        assert "/etc/ssl/cert.pem" in content, (
            "setup_ca_trust must support Alpine CA bundle path"
        )
        assert "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem" in content, (
            "setup_ca_trust must support RHEL/CentOS CA bundle path"
        )

    def test_setup_ca_trust_has_fallback_without_proxy_ca(self) -> None:
        """setup_ca_trust must fall back to system-only bundle without proxy CA."""
        content = ENTRYPOINT_LIB_CREDENTIALS_PATH.read_text()
        # The function must have an else branch that copies just the system
        # bundle when the proxy CA cert is absent, so SSL_CERT_FILE always
        # points to a valid file.
        func_start = content.find("setup_ca_trust()")
        func_body = content[func_start:]
        assert "else" in func_body, (
            "setup_ca_trust must have an else branch for the no-proxy-CA case"
        )
        assert "cp " in func_body, (
            "setup_ca_trust must cp the system bundle when proxy CA is absent"
        )

    def test_path_list_matches_shared_constant(self) -> None:
        """Entrypoint CA paths must match SYS_CA_BUNDLE_PATHS in shared.py."""
        import re

        from paude.backends.shared import SYS_CA_BUNDLE_PATHS

        content = ENTRYPOINT_LIB_CREDENTIALS_PATH.read_text()
        # Extract paths from the _find_sys_ca_bundle function
        func_start = content.find("_find_sys_ca_bundle()")
        func_end = content.find("}", func_start)
        func_body = content[func_start:func_end]
        paths = [p.rstrip(";") for p in re.findall(r"(/etc/\S+)", func_body)]
        assert tuple(paths) == SYS_CA_BUNDLE_PATHS, (
            f"CA bundle paths in entrypoint ({paths}) must match "
            f"SYS_CA_BUNDLE_PATHS in shared.py ({SYS_CA_BUNDLE_PATHS})"
        )


class TestSetupCaTrustFunctional:
    """Functional tests: run setup_ca_trust in a temp directory."""

    def _build_ca_trust_script(
        self,
        tmp_path: Path,
        *,
        sys_bundle_path: str,
        has_proxy_ca: bool = True,
    ) -> str:
        """Build a script that exercises setup_ca_trust with custom paths."""
        import textwrap

        # Create fake system CA bundle
        bundle_dir = Path(sys_bundle_path).parent
        (tmp_path / bundle_dir.relative_to("/")).mkdir(parents=True, exist_ok=True)
        sys_bundle_file = tmp_path / Path(sys_bundle_path).relative_to("/")
        sys_bundle_file.write_text("SYSTEM-CA-BUNDLE\n")

        # Create fake proxy CA cert
        proxy_ca_dir = tmp_path / "etc/pki/ca-trust/source/anchors"
        proxy_ca_dir.mkdir(parents=True, exist_ok=True)
        if has_proxy_ca:
            (proxy_ca_dir / "paude-proxy-ca.crt").write_text("PROXY-CA-CERT\n")

        custom_bundle = tmp_path / "tmp" / "paude-ca-bundle.pem"
        (tmp_path / "tmp").mkdir(exist_ok=True)

        return textwrap.dedent(f"""\
            #!/bin/bash
            set -e

            _find_sys_ca_bundle() {{
                local path
                for path in \\
                    {tmp_path}/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem \\
                    {tmp_path}/etc/ssl/certs/ca-certificates.crt \\
                    {tmp_path}/etc/ssl/ca-bundle.pem \\
                    {tmp_path}/etc/ssl/cert.pem; do
                    if [[ -f "$path" ]]; then
                        echo "$path"
                        return 0
                    fi
                done
                return 1
            }}

            setup_ca_trust() {{
                local ca_cert="{proxy_ca_dir}/paude-proxy-ca.crt"
                local custom_bundle="{custom_bundle}"
                local sys_bundle
                sys_bundle=$(_find_sys_ca_bundle) || return 0

                if [[ -f "$ca_cert" ]]; then
                    cat "$sys_bundle" "$ca_cert" > "$custom_bundle" 2>/dev/null || true
                else
                    cp "$sys_bundle" "$custom_bundle" 2>/dev/null || true
                fi
            }}

            setup_ca_trust
        """)

    def test_creates_bundle_with_centos_path(self, tmp_path: Path) -> None:
        """Bundle created when system CA is at RHEL/CentOS path."""
        script = self._build_ca_trust_script(
            tmp_path,
            sys_bundle_path="/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        bundle = (tmp_path / "tmp" / "paude-ca-bundle.pem").read_text()
        assert "SYSTEM-CA-BUNDLE" in bundle
        assert "PROXY-CA-CERT" in bundle

    def test_creates_bundle_with_debian_path(self, tmp_path: Path) -> None:
        """Bundle created when system CA is at Debian/Ubuntu path."""
        script = self._build_ca_trust_script(
            tmp_path,
            sys_bundle_path="/etc/ssl/certs/ca-certificates.crt",
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        bundle = (tmp_path / "tmp" / "paude-ca-bundle.pem").read_text()
        assert "SYSTEM-CA-BUNDLE" in bundle
        assert "PROXY-CA-CERT" in bundle

    def test_creates_bundle_with_alpine_path(self, tmp_path: Path) -> None:
        """Bundle created when system CA is at Alpine path."""
        script = self._build_ca_trust_script(
            tmp_path,
            sys_bundle_path="/etc/ssl/cert.pem",
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        bundle = (tmp_path / "tmp" / "paude-ca-bundle.pem").read_text()
        assert "SYSTEM-CA-BUNDLE" in bundle
        assert "PROXY-CA-CERT" in bundle

    def test_creates_bundle_without_proxy_ca(self, tmp_path: Path) -> None:
        """Bundle created from system CAs only when proxy CA is absent."""
        script = self._build_ca_trust_script(
            tmp_path,
            sys_bundle_path="/etc/ssl/certs/ca-certificates.crt",
            has_proxy_ca=False,
        )
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        bundle = (tmp_path / "tmp" / "paude-ca-bundle.pem").read_text()
        assert "SYSTEM-CA-BUNDLE" in bundle
        assert "PROXY-CA-CERT" not in bundle

    def test_no_bundle_when_no_system_ca(self, tmp_path: Path) -> None:
        """No bundle created when no system CA bundle exists."""
        import textwrap

        custom_bundle = tmp_path / "tmp" / "paude-ca-bundle.pem"
        (tmp_path / "tmp").mkdir(exist_ok=True)

        script = textwrap.dedent(f"""\
            #!/bin/bash
            _find_sys_ca_bundle() {{
                return 1
            }}
            setup_ca_trust() {{
                local ca_cert="/nonexistent"
                local custom_bundle="{custom_bundle}"
                local sys_bundle
                sys_bundle=$(_find_sys_ca_bundle) || return 0
                cat "$sys_bundle" > "$custom_bundle" 2>/dev/null || true
            }}
            setup_ca_trust
        """)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr
        assert not custom_bundle.exists()


class TestGenerateSandboxConfigScript:
    """Tests for generate_sandbox_config_script() in shared.py."""

    def test_generates_claude_script(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script("claude", "/pvc/workspace", "")
        assert "hasCompletedOnboarding" in script
        assert "hasTrustDialogAccepted" in script

    def test_generates_gemini_script(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script("gemini", "/pvc/workspace", "")
        assert "trustedFolders.json" in script
        assert "TRUST_FOLDER" in script

    def test_generates_cursor_script(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script("cursor", "/pvc/workspace", "")
        assert "cli-config.json" in script
        assert "workspace-trusted" in script

    def test_claude_script_uses_container_home(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script("claude", "/pvc/workspace", "")
        assert "/home/paude/.claude.json" in script

    def test_claude_script_with_yolo_flag(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script(
            "claude", "/pvc/workspace", "", yolo=True
        )
        assert "skipDangerousModePermissionPrompt" in script

    def test_generates_openclaw_hardened_script(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script("openclaw", "/pvc/workspace", "")
        assert '"host": "gateway"' in script
        assert '"security": "allowlist"' in script
        assert '"ask": "on-miss"' in script
        assert '"workspaceOnly": true' in script

    def test_generates_openclaw_yolo_script(self) -> None:
        from paude.backends.shared import generate_sandbox_config_script

        script = generate_sandbox_config_script(
            "openclaw", "/pvc/workspace", "", yolo=True
        )
        assert '"host": "gateway"' in script
        assert '"security": "full"' in script
        assert '"ask": "off"' in script
