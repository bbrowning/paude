"""Tests for entrypoint-session.sh seed copy logic (Podman backend).

These tests exercise the bash seed copy block by extracting it into a
minimal script, running it in a temporary directory, and verifying results.

A contract test also validates that entrypoint-session.sh itself contains the
expected cp -a pattern and not the old file-by-file loop.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

# Path to the real entrypoint, used by contract tests
ENTRYPOINT_PATH = (
    Path(__file__).parent.parent / "containers" / "paude" / "entrypoint-session.sh"
)


def _build_script(home_dir: str, seed_dir: str, credentials_dir: str | None) -> str:
    """Build a minimal bash script that replicates the seed copy logic.

    Args:
        home_dir: Path to use as HOME.
        seed_dir: Path to use as /tmp/claude.seed.
        credentials_dir: Path to use as /credentials, or None to skip.
            When None, CRED_DIR is set to a non-existent path under home_dir.
    """
    # Guard: if credentials_dir is set, create it so the -d test passes
    credentials_check = ""
    if credentials_dir is not None:
        credentials_check = f'mkdir -p "{credentials_dir}"'

    # When no credentials_dir, use a guaranteed-nonexistent path under tmp_path
    cred_dir_value = credentials_dir or f"{home_dir}/.no-credentials"

    return textwrap.dedent(f"""\
        #!/bin/bash
        set -e
        export HOME="{home_dir}"
        SEED_DIR="{seed_dir}"
        CRED_DIR="{cred_dir_value}"
        {credentials_check}

        # Replicate the seed copy block from entrypoint-session.sh
        if [[ -d "$SEED_DIR" ]] && [[ ! -d "$CRED_DIR" ]]; then
            mkdir -p "$HOME/.claude"
            chmod g+rwX "$HOME/.claude" 2>/dev/null || true

            cp -a "$SEED_DIR/." "$HOME/.claude/" 2>/dev/null || true

            if [[ -f "$HOME/.claude/claude.json" ]]; then
                mv "$HOME/.claude/claude.json" "$HOME/.claude.json" 2>/dev/null || true
                chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
            fi

            if [[ -d "$HOME/.claude/plugins" ]]; then
                chmod -R g+rwX "$HOME/.claude/plugins" 2>/dev/null || true
            fi

            chmod -R g+rwX "$HOME/.claude" 2>/dev/null || true
        fi
    """)


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

    def test_entrypoint_uses_cp_archive(self) -> None:
        """The entrypoint must use 'cp -a' for seed copy, not a file loop."""
        content = ENTRYPOINT_PATH.read_text()
        assert (
            "cp -a /tmp/claude.seed/." in content
            or 'cp -a "$SEED_DIR/."' in content
            or "cp -a /tmp/claude.seed/" in content
            or 'cp -a "$AGENT_SEED_DIR/."' in content
        ), "entrypoint-session.sh must use 'cp -a' for recursive seed copy"

    def test_entrypoint_has_apply_sandbox_config(self) -> None:
        """The entrypoint must contain the apply_sandbox_config function."""
        content = ENTRYPOINT_PATH.read_text()
        assert "apply_sandbox_config()" in content, (
            "entrypoint-session.sh must define apply_sandbox_config()"
        )
        assert "hasCompletedOnboarding" in content, (
            "apply_sandbox_config must set hasCompletedOnboarding"
        )
        assert "hasTrustDialogAccepted" in content, (
            "apply_sandbox_config must set hasTrustDialogAccepted"
        )
        assert "skipDangerousModePermissionPrompt" in content, (
            "apply_sandbox_config must set skipDangerousModePermissionPrompt"
        )

    def test_entrypoint_no_old_file_loop(self) -> None:
        """The old file-by-file loop pattern must not be present."""
        content = ENTRYPOINT_PATH.read_text()
        assert "for f in /tmp/claude.seed/*" not in content, (
            "entrypoint-session.sh still contains the old file-by-file loop"
        )

    def test_entrypoint_handles_claude_json_after_copy(self) -> None:
        """Config file must be moved (not copied separately) after cp -a."""
        content = ENTRYPOINT_PATH.read_text()
        # Scope to the Podman seed block (uses $AGENT_SEED_DIR or /tmp/claude.seed)
        cp_pos = content.find('cp -a "$AGENT_SEED_DIR/."')
        if cp_pos == -1:
            cp_pos = content.find("cp -a /tmp/claude.seed/.")
        assert cp_pos != -1, "Missing cp -a command for seed dir"
        # Find the mv that comes after this specific cp -a
        mv_pos = max(
            content.find("AGENT_CONFIG_FILE_BASENAME", cp_pos + 1),
            content.find("claude.json", cp_pos + 1),
        )
        assert mv_pos != -1, "Missing mv command for config file after cp -a"
        assert mv_pos > cp_pos, "mv must come after cp -a"


class TestSeedCopyRegularFiles:
    """Test that regular files are copied from seed."""

    def test_copies_regular_files(self, tmp_path: Path) -> None:
        """Regular files like settings.json are copied to ~/.claude/."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        (seed / "settings.json").write_text('{"key": "value"}')
        (seed / "projects.json").write_text("[]")

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude" / "settings.json").read_text() == '{"key": "value"}'
        assert (home / ".claude" / "projects.json").read_text() == "[]"


class TestSeedCopyDirectories:
    """Test that directories (like commands/) are recursively copied."""

    def test_copies_directories_recursively(self, tmp_path: Path) -> None:
        """Directories like commands/ with nested subdirs are fully copied."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        # Create commands/ with nested structure
        commands = seed / "commands"
        commands.mkdir()
        (commands / "skill1.md").write_text("# Skill 1")

        subdir = commands / "subdir"
        subdir.mkdir()
        (subdir / "skill2.md").write_text("# Skill 2")

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude" / "commands" / "skill1.md").read_text() == "# Skill 1"
        assert (
            home / ".claude" / "commands" / "subdir" / "skill2.md"
        ).read_text() == "# Skill 2"


class TestSeedCopyHiddenFiles:
    """Test that hidden files (dotfiles) are copied.

    The old glob-based loop (for f in seed/*) skipped hidden files.
    cp -a copies everything including dotfiles, which is the desired behavior.
    """

    def test_copies_dotfiles(self, tmp_path: Path) -> None:
        """Hidden files like .gitignore inside seed are copied."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        (seed / ".some-hidden-config").write_text("hidden")
        (seed / "settings.json").write_text("{}")

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude" / ".some-hidden-config").read_text() == "hidden"
        assert (home / ".claude" / "settings.json").read_text() == "{}"


class TestSeedCopySymlinks:
    """Test symlink handling with cp -a.

    cp -a preserves symlinks (unlike the old cp -L which dereferenced them).
    This matches the OpenShift backend behavior. Symlinks to files within the
    seed tree should work; symlinks pointing outside will be preserved as-is.
    """

    def test_copies_symlinks_to_local_targets(self, tmp_path: Path) -> None:
        """Symlinks pointing within the seed tree are preserved and functional."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        (seed / "real-file.json").write_text('{"real": true}')
        (seed / "link-to-file.json").symlink_to("real-file.json")

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        link_dest = home / ".claude" / "link-to-file.json"
        assert link_dest.is_symlink()
        assert link_dest.read_text() == '{"real": true}'


class TestSeedCopyClaudeJson:
    """Test claude.json special handling."""

    def test_claude_json_moved_to_home_root(self, tmp_path: Path) -> None:
        """claude.json ends up at ~/.claude.json, not ~/.claude/claude.json."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        (seed / "claude.json").write_text('{"config": true}')

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude.json").read_text() == '{"config": true}'
        assert not (home / ".claude" / "claude.json").exists()

    def test_other_files_unaffected_by_claude_json_move(self, tmp_path: Path) -> None:
        """Other files aren't disturbed when claude.json is moved."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        (seed / "claude.json").write_text('{"config": true}')
        (seed / "settings.json").write_text('{"settings": true}')

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude" / "settings.json").read_text() == '{"settings": true}'
        assert (home / ".claude.json").read_text() == '{"config": true}'


class TestSeedCopySkipsWithCredentials:
    """Test that seed copy is skipped when /credentials exists."""

    def test_skips_when_credentials_dir_exists(self, tmp_path: Path) -> None:
        """No copy happens when credentials directory exists (OpenShift path)."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()
        cred = tmp_path / "credentials"
        # cred dir will be created by the script

        (seed / "settings.json").write_text('{"key": "value"}')

        script = _build_script(str(home), str(seed), str(cred))
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert not (home / ".claude").exists()


class TestSeedCopyEmptySeed:
    """Test behavior with an empty seed directory."""

    def test_empty_seed_creates_claude_dir_without_error(self, tmp_path: Path) -> None:
        """Empty seed directory should succeed and create ~/.claude/."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()
        # seed is intentionally empty

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        assert (home / ".claude").is_dir()
        # No claude.json should appear
        assert not (home / ".claude.json").exists()


class TestSeedCopyMixedContent:
    """Test copying a mix of files and directories."""

    def test_copies_files_and_directories_together(self, tmp_path: Path) -> None:
        """Mix of files, directories, and nested content all get copied."""
        home = tmp_path / "home"
        home.mkdir()
        seed = tmp_path / "seed"
        seed.mkdir()

        # Regular files
        (seed / "settings.json").write_text('{"settings": true}')
        (seed / "claude.json").write_text('{"claude": true}')

        # Directory with files
        commands = seed / "commands"
        commands.mkdir()
        (commands / "my-skill.md").write_text("# My Skill")

        # Plugins directory
        plugins = seed / "plugins"
        plugins.mkdir()
        (plugins / "plugin.json").write_text('{"plugin": true}')

        script = _build_script(str(home), str(seed), None)
        result = _run_script(script)
        assert result.returncode == 0, result.stderr

        # Regular file copied
        assert (home / ".claude" / "settings.json").read_text() == '{"settings": true}'
        # claude.json moved to home root
        assert (home / ".claude.json").read_text() == '{"claude": true}'
        assert not (home / ".claude" / "claude.json").exists()
        # Directory copied
        assert (
            home / ".claude" / "commands" / "my-skill.md"
        ).read_text() == "# My Skill"
        # Plugins directory copied
        assert (
            home / ".claude" / "plugins" / "plugin.json"
        ).read_text() == '{"plugin": true}'


def _build_sandbox_script(
    home_dir: str,
    workspace: str,
    suppress_prompts: bool,
    claude_args: str = "",
) -> str:
    """Build a script that replicates the apply_sandbox_config logic."""
    env_lines = f'export HOME="{home_dir}"\n'
    env_lines += f'export PAUDE_WORKSPACE="{workspace}"\n'
    if suppress_prompts:
        env_lines += 'export PAUDE_SUPPRESS_PROMPTS="1"\n'
    else:
        env_lines += "unset PAUDE_SUPPRESS_PROMPTS 2>/dev/null || true\n"
    if claude_args:
        env_lines += f'export PAUDE_CLAUDE_ARGS="{claude_args}"\n'
    else:
        env_lines += "unset PAUDE_CLAUDE_ARGS 2>/dev/null || true\n"

    return textwrap.dedent(f"""\
        #!/bin/bash
        set -e
        {env_lines}
        apply_sandbox_config() {{
            if [[ "${{PAUDE_SUPPRESS_PROMPTS:-}}" != "1" ]]; then
                return 0
            fi

            local workspace="${{PAUDE_WORKSPACE:-/workspace}}"
            local claude_json="$HOME/.claude.json"
            local settings_json="$HOME/.claude/settings.json"

            if [[ -f "$claude_json" ]]; then
                jq --arg ws "$workspace" '. * {{
                    hasCompletedOnboarding: true,
                    projects: {{($ws): {{hasTrustDialogAccepted: true}}}}
                }}' "$claude_json" > "${{claude_json}}.tmp" \\
                    && mv "${{claude_json}}.tmp" "$claude_json"
            else
                jq -n --arg ws "$workspace" '{{
                    hasCompletedOnboarding: true,
                    projects: {{($ws): {{hasTrustDialogAccepted: true}}}}
                }}' > "$claude_json"
            fi

            if [[ "${{PAUDE_CLAUDE_ARGS:-}}" == *"--dangerously-skip-permissions"* ]]; then
                mkdir -p "$HOME/.claude" 2>/dev/null || true
                local skip_patch='{{"skipDangerousModePermissionPrompt": true}}'
                if [[ -f "$settings_json" ]]; then
                    jq --argjson patch "$skip_patch" '. * $patch' "$settings_json" > "${{settings_json}}.tmp" \\
                        && mv "${{settings_json}}.tmp" "$settings_json"
                else
                    echo "$skip_patch" > "$settings_json"
                fi
            fi
        }}

        apply_sandbox_config
    """)


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
            claude_args="--dangerously-skip-permissions",
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
            claude_args="--dangerously-skip-permissions",
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

    def test_no_settings_json_without_skip_permissions(self, tmp_path: Path) -> None:
        """No settings.json changes when --dangerously-skip-permissions not in args."""
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
