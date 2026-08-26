"""Tests for CLI argument parsing."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from paude.backends import Session
from paude.cli import _parse_copy_path, app

runner = CliRunner()


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--help", id="long-flag"),
        pytest.param("-h", id="short-flag"),
    ],
)
def test_help_shows_help(flag):
    """Help flag shows help and exits 0."""
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "Run AI coding agents in isolated containers" in result.stdout


@pytest.mark.parametrize(
    "flag",
    [
        pytest.param("--version", id="long-flag"),
        pytest.param("-V", id="short-flag"),
    ],
)
def test_version_shows_version(flag):
    """Version flag shows version and exits 0."""
    from paude import __version__

    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert f"paude {__version__}" in result.stdout


def test_version_shows_development_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'development' when PAUDE_DEV=1."""
    monkeypatch.setenv("PAUDE_DEV", "1")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "development" in result.stdout
    assert "PAUDE_DEV=1" in result.stdout


def test_version_shows_installed_mode(monkeypatch: pytest.MonkeyPatch):
    """--version shows 'installed' when PAUDE_DEV=0."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "installed" in result.stdout
    assert "quay.io/bbrowning" in result.stdout


def test_version_shows_custom_registry(monkeypatch: pytest.MonkeyPatch):
    """--version shows custom registry when PAUDE_REGISTRY is set."""
    monkeypatch.setenv("PAUDE_DEV", "0")
    monkeypatch.setenv("PAUDE_REGISTRY", "ghcr.io/custom")
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ghcr.io/custom" in result.stdout


def test_dry_run_works():
    """--dry-run works and shows config info."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.stdout


def test_dry_run_shows_no_config():
    """--dry-run shows 'none' when no config file exists."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "Configuration: none" in result.stdout


def test_dry_run_shows_flag_states():
    """--dry-run shows flag states."""
    result = runner.invoke(
        app, ["create", "--yolo", "--allowed-domains", "all", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "yolo: True" in result.stdout
    assert "allowed-domains: unrestricted" in result.stdout


def test_dry_run_shows_gpu():
    """--dry-run shows gpu when --gpu is specified."""
    result = runner.invoke(app, ["create", "--gpu", "all", "--dry-run"])
    assert result.exit_code == 0
    assert "gpu: all" in result.stdout


def test_dry_run_gpu_device_spec():
    """--dry-run shows gpu device spec."""
    result = runner.invoke(app, ["create", "--gpu", "device=0,1", "--dry-run"])
    assert result.exit_code == 0
    assert "gpu: device=0,1" in result.stdout


def test_dry_run_no_gpu_hides_gpu():
    """--dry-run does not show gpu when --no-gpu is specified."""
    result = runner.invoke(app, ["create", "--no-gpu", "--dry-run"])
    assert result.exit_code == 0
    assert "gpu:" not in result.stdout


def test_create_rejects_forward_port():
    """--forward-port has moved off create onto connect/start."""
    result = runner.invoke(app, ["create", "--forward-port", "8372", "--dry-run"])
    assert result.exit_code != 0
    assert "No such option" in result.output


@pytest.mark.parametrize("command", ["connect", "start"])
def test_forward_port_recognized_on_attach_commands(command):
    """--forward-port is accepted by both attach commands."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "--forward-port" in _strip_ansi(result.stdout)


@pytest.mark.parametrize("command", ["connect", "start"])
def test_forward_port_invalid_spec_errors(command):
    """An invalid --forward-port spec fails with a clear error before attach."""
    result = runner.invoke(
        app, [command, "test-session", "--forward-port", "not-a-port"]
    )
    assert result.exit_code == 1
    # Error goes to stderr, which typer may redirect to stdout
    output = result.stdout + (result.stderr or "")
    assert "invalid port spec" in output


@pytest.mark.parametrize("exit_code", [0, 2])
def test_start_autodetect_propagates_exit_code(exit_code):
    """A clean start propagates start_session's exit code as-is.

    Regression: start_session's result was raised via typer.Exit *inside* a
    try/except Exception. Since typer.Exit subclasses Exception, a successful
    exit was caught and reported as "Error starting session: 0" with exit 1.
    """
    from paude.cli.app import BackendType

    backend_obj = MagicMock()
    backend_obj.start_session.return_value = exit_code
    with patch(
        "paude.cli.commands.lifecycle.find_session_backend",
        return_value=(BackendType.podman, backend_obj),
    ):
        result = runner.invoke(app, ["start", "my-session"])
    assert result.exit_code == exit_code
    output = result.stdout + (result.stderr or "")
    assert "Error starting session" not in output
    backend_obj.start_session.assert_called_once()


@pytest.mark.parametrize("exit_code", [0, 2])
def test_start_explicit_backend_propagates_exit_code(exit_code):
    """Same regression guard for the explicit --backend try/except site."""
    backend_obj = MagicMock()
    backend_obj.start_session.return_value = exit_code
    with patch(
        "paude.cli.commands.lifecycle._get_backend_instance",
        return_value=backend_obj,
    ):
        result = runner.invoke(app, ["start", "my-session", "--backend", "podman"])
    assert result.exit_code == exit_code
    output = result.stdout + (result.stderr or "")
    assert "Error starting session" not in output
    backend_obj.start_session.assert_called_once()


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        pytest.param("--yolo", "yolo", id="yolo"),
        pytest.param("--rebuild", "rebuild", id="rebuild"),
        pytest.param("--verbose", "verbose", id="verbose"),
    ],
)
def test_flag_recognized(flag, name):
    """Boolean flags are recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", flag, "--dry-run"])
    assert result.exit_code == 0
    assert f"{name}: True" in result.stdout


def test_allowed_domains_default_value():
    """Default --allowed-domains value shows vertexai + python."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "allowed-domains:" in result.stdout
    # Default should expand to vertexai + python
    assert "vertexai" in result.stdout or "python" in result.stdout


def test_allowed_domains_all_value():
    """--allowed-domains all shows unrestricted."""
    result = runner.invoke(app, ["create", "--allowed-domains", "all", "--dry-run"])
    assert result.exit_code == 0
    assert "allowed-domains: unrestricted" in result.stdout


def test_allowed_domains_custom_domain():
    """--allowed-domains with custom domain."""
    result = runner.invoke(
        app, ["create", "--allowed-domains", ".example.com", "--dry-run"]
    )
    assert result.exit_code == 0
    assert ".example.com" in result.stdout


def test_allowed_domains_multiple_values():
    """--allowed-domains can be repeated."""
    result = runner.invoke(
        app,
        [
            "create",
            "--allowed-domains",
            "vertexai",
            "--allowed-domains",
            ".example.com",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    # Should show both
    assert "vertexai" in result.stdout or ".example.com" in result.stdout


def test_help_shows_dry_run_option():
    """--help shows --dry-run option."""
    result = runner.invoke(app, ["--help"])
    assert "--dry-run" in result.stdout


def test_args_option():
    """--args option is parsed and captured in claude_args (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--dry-run", "--args", "-p hello"])
    assert result.exit_code == 0
    assert "args: ['-p', 'hello']" in result.stdout


def test_multiple_flags_work_together():
    """Multiple flags work together (verified via dry-run)."""
    result = runner.invoke(
        app, ["create", "--yolo", "--allowed-domains", "all", "--rebuild", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "yolo: True" in result.stdout
    assert "allowed-domains: unrestricted" in result.stdout
    assert "rebuild: True" in result.stdout


def test_backend_flag_recognized():
    """--backend flag is recognized (verified via dry-run)."""
    result = runner.invoke(app, ["create", "--backend=podman", "--dry-run"])
    assert result.exit_code == 0
    assert "backend: podman" in result.stdout


def test_github_domains_in_default_dry_run():
    """GitHub domains appear in dry-run output by default (github is in DEFAULT_ALIASES)."""
    result = runner.invoke(app, ["create", "--dry-run"])
    assert result.exit_code == 0
    assert "github" in result.stdout


def _extract_domains_display(stdout: str) -> str:
    """Extract the allowed-domains value from dry-run output."""
    parts = stdout.split("allowed-domains:")
    assert len(parts) > 1, f"allowed-domains not found in output:\n{stdout}"
    return parts[1].split("\n")[0].strip()


class TestAgentSpecificDomainExpansion:
    """Verify that --agent affects which default domains are expanded."""

    @pytest.fixture
    def claude_dry_run(self):
        result = runner.invoke(app, ["create", "--agent", "claude", "--dry-run"])
        assert result.exit_code == 0
        return result

    @pytest.fixture
    def gemini_dry_run(self):
        result = runner.invoke(app, ["create", "--agent", "gemini", "--dry-run"])
        assert result.exit_code == 0
        return result

    def test_claude_default_includes_claude_alias(self, claude_dry_run):
        """--agent claude default domains include claude alias."""
        assert "claude" in _extract_domains_display(claude_dry_run.stdout)

    def test_claude_default_excludes_gemini_alias(self, claude_dry_run):
        """--agent claude default domains exclude gemini alias."""
        assert "gemini" not in _extract_domains_display(claude_dry_run.stdout)

    def test_gemini_default_includes_gemini_alias(self, gemini_dry_run):
        """--agent gemini default domains include gemini alias."""
        assert "gemini" in _extract_domains_display(gemini_dry_run.stdout)

    def test_gemini_default_includes_nodejs_alias(self, gemini_dry_run):
        """--agent gemini default domains include nodejs alias."""
        assert "nodejs" in _extract_domains_display(gemini_dry_run.stdout)

    def test_gemini_default_excludes_claude_alias(self, gemini_dry_run):
        """--agent gemini default domains exclude claude alias."""
        assert "claude" not in _extract_domains_display(gemini_dry_run.stdout)

    def test_both_agents_include_shared_base_aliases(
        self, claude_dry_run, gemini_dry_run
    ):
        """Both agents include vertexai, python, and github in defaults."""
        for base in ["vertexai", "python", "github"]:
            assert base in claude_dry_run.stdout, f"{base} missing from claude"
            assert base in gemini_dry_run.stdout, f"{base} missing from gemini"

    def test_explicit_domains_override_agent_defaults(self):
        """Explicit --allowed-domains ignores agent-specific defaults."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agent",
                "gemini",
                "--allowed-domains",
                "vertexai",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "gemini" not in _extract_domains_display(result.stdout)

    @pytest.mark.parametrize(
        ("provider", "expect_codex_alias"),
        [
            pytest.param("chatgpt", True, id="chatgpt-forces-alias-in"),
            pytest.param("openai", False, id="openai-excludes-alias"),
        ],
    )
    def test_codex_explicit_domains_oauth_requirements(
        self, provider: str, expect_codex_alias: bool
    ):
        """Only --provider chatgpt forces the codex alias in over custom domains."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agent",
                "codex",
                "--provider",
                provider,
                "--allowed-domains",
                ".example.com",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        output = _extract_domains_display(result.stdout)
        assert ("chatgpt" in output) is expect_codex_alias


class TestCodexChatgptProvider:
    """Tests for `--agent codex --provider chatgpt`."""

    @pytest.mark.parametrize("provider", ["chatgpt", "openai"])
    def test_codex_provider_dry_run(self, provider: str):
        result = runner.invoke(
            app, ["create", "--agent", "codex", "--provider", provider, "--dry-run"]
        )
        assert result.exit_code == 0
        assert f"provider: {provider}" in result.stdout

    def test_codex_invalid_provider_rejected(self):
        result = runner.invoke(
            app, ["create", "--agent", "codex", "--provider", "vertex", "--dry-run"]
        )
        assert result.exit_code != 0


class TestAnthropicOAuthProvider:
    """Tests for the `anthropic-oauth` (Anthropic Max plan) provider."""

    def test_claude_anthropic_oauth_dry_run(self):
        result = runner.invoke(
            app,
            [
                "create",
                "--agent",
                "claude",
                "--provider",
                "anthropic-oauth",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "provider: anthropic-oauth" in _strip_ansi(result.stdout)

    def test_gascity_claude_codex_swap_to_anthropic_oauth(self):
        """The user's flow: claude + gascity on anthropic-oauth, codex on chatgpt."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agents",
                "gascity,claude,codex",
                "--agent-provider",
                "gascity=anthropic-oauth,claude=anthropic-oauth,codex=chatgpt",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        out = _strip_ansi(result.stdout)
        assert "gascity -> anthropic-oauth" in out
        assert "claude -> anthropic-oauth" in out
        assert "codex -> chatgpt" in out

    def test_codex_anthropic_oauth_rejected(self):
        """anthropic-oauth is not a valid provider for codex."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agent",
                "codex",
                "--provider",
                "anthropic-oauth",
                "--dry-run",
            ],
        )
        assert result.exit_code != 0


class TestAgentsProvidersLists:
    """Tests for the list-valued --agents/--providers options."""

    def test_agents_providers_dry_run(self):
        """--agents/--providers show both lists and per-agent providers."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agents",
                "gascity,claude,codex",
                "--providers",
                "vertex,chatgpt",
                "--agent-provider",
                "codex=chatgpt",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        out = _strip_ansi(result.stdout)
        assert "agents: gascity, claude, codex" in out
        assert "credential providers: vertex, chatgpt" in out
        # Derived per-agent providers.
        assert "gascity -> vertex" in out
        assert "claude -> vertex" in out
        assert "codex -> chatgpt" in out

    def test_agents_repeatable_option(self):
        """--agents can be repeated as well as comma-separated."""
        result = runner.invoke(
            app,
            ["create", "--agents", "gascity", "--agents", "claude", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "agents: gascity, claude" in _strip_ansi(result.stdout)

    def test_singular_agent_alias_dry_run(self):
        """--agent still resolves to a single-item agents list."""
        result = runner.invoke(app, ["create", "--agent", "gascity", "--dry-run"])
        assert result.exit_code == 0
        assert "agents: gascity" in _strip_ansi(result.stdout)

    @patch("paude.dry_run.show_dry_run")
    def test_single_gascity_install_is_exact(self, mock_show: MagicMock):
        """Gas City no longer expands implicit child CLIs during creation."""
        result = runner.invoke(app, ["create", "--agent", "gascity", "--dry-run"])

        assert result.exit_code == 0
        assert mock_show.call_args.kwargs["composition"].names == ["gascity"]

    def test_duplicate_agents_rejected(self):
        """Duplicate installed agents fail clearly."""
        result = runner.invoke(
            app, ["create", "--agents", "claude,claude,codex", "--dry-run"]
        )
        assert result.exit_code != 0
        assert "Duplicate agent" in _strip_ansi(result.output)

    def test_agent_and_agents_conflict(self):
        """Passing both --agent and --agents fails with a clear message."""
        result = runner.invoke(
            app, ["create", "--agent", "claude", "--agents", "codex", "--dry-run"]
        )
        assert result.exit_code != 0
        assert "not both" in _strip_ansi(result.output)

    def test_provider_and_providers_are_independent(self):
        """Primary mapping shorthand can use an explicit credential set."""
        result = runner.invoke(
            app,
            [
                "create",
                "--provider",
                "vertex",
                "--providers",
                "vertex,openai",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "credential providers: vertex, openai" in out
        assert "claude -> vertex" in out

    def test_provider_and_agent_provider_conflict(self):
        result = runner.invoke(
            app,
            [
                "create",
                "--provider",
                "vertex",
                "--agent-provider",
                "claude=anthropic",
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "not both" in _strip_ansi(result.output)

    @pytest.mark.parametrize(
        "mapping",
        ["codex", "=openai", "codex=", "codex=openai=extra"],
    )
    def test_malformed_agent_provider_rejected(self, mapping: str):
        result = runner.invoke(
            app,
            ["create", "--agent-provider", mapping, "--dry-run"],
        )
        assert result.exit_code != 0
        assert "expected AGENT=PROVIDER" in _strip_ansi(result.output)

    def test_duplicate_agent_provider_mapping_rejected(self):
        result = runner.invoke(
            app,
            [
                "create",
                "--agents",
                "claude,codex",
                "--agent-provider",
                "codex=openai,codex=chatgpt",
                "--dry-run",
            ],
        )
        assert result.exit_code != 0
        assert "Duplicate provider mapping" in _strip_ansi(result.output)

    def test_unknown_agent_rejected(self):
        """An unknown agent name in --agents is rejected."""
        result = runner.invoke(
            app, ["create", "--agents", "not-a-real-agent", "--dry-run"]
        )
        assert result.exit_code != 0

    @patch("paude.cli.create_podman.create_podman_session")
    @patch("paude.cli.create._prepare_session_create")
    def test_multi_agent_real_create_passes_full_composition(
        self, mock_prepare, mock_create
    ):
        """A real create passes every requested agent and provider onward."""
        mock_prepare.return_value = ([], [], {}, False)
        result = runner.invoke(app, ["create", "--agents", "claude,codex,gascity"])
        assert result.exit_code == 0
        out = _strip_ansi(result.output)
        assert "multi-agent creation is not yet supported" not in out
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["agent_name"] == "claude"
        assert mock_create.call_args.kwargs["agent_providers"] == [
            ("claude", "vertex"),
            ("codex", "chatgpt"),
            ("gascity", "vertex"),
        ]
        assert mock_create.call_args.kwargs["credential_providers"] == [
            "vertex",
            "chatgpt",
        ]

    @patch("paude.cli.create_podman.create_podman_session")
    @patch("paude.cli.create._prepare_session_create")
    def test_single_agent_real_create_no_warning(self, mock_prepare, mock_create):
        """A real create with a single agent emits no multi-agent warning."""
        mock_prepare.return_value = ([], [], {}, False)
        result = runner.invoke(app, ["create", "--agents", "claude"])
        assert result.exit_code == 0
        assert "multi-agent creation is not yet supported" not in _strip_ansi(
            result.output
        )
        mock_create.assert_called_once()

    def test_empty_agent_rejected_cleanly(self):
        """An explicit empty --agent fails with a clean error, not a traceback."""
        result = runner.invoke(app, ["create", "--agent", "", "--dry-run"])
        assert result.exit_code != 0
        assert result.exception is None or not isinstance(result.exception, IndexError)
        assert "Agent name cannot be empty" in _strip_ansi(result.output)

    def test_empty_provider_rejected_cleanly(self):
        """An explicit empty --provider fails with a clean error, not a silent default."""
        result = runner.invoke(app, ["create", "--provider", "", "--dry-run"])
        assert result.exit_code != 0
        assert "Provider name cannot be empty" in _strip_ansi(result.output)

    @patch("paude.cli.create_podman.create_podman_session")
    @patch("paude.cli.create._prepare_session_create")
    def test_explicit_mappings_and_extra_credentials_pass_to_create(
        self, mock_prepare, mock_create
    ):
        """Mappings and extra credentials remain independent."""
        mock_prepare.return_value = ([], [], {}, False)
        result = runner.invoke(
            app,
            [
                "create",
                "--agents",
                "claude,codex",
                "--providers",
                "anthropic,openai,vertex",
                "--agent-provider",
                "claude=anthropic,codex=openai",
            ],
        )
        assert result.exit_code == 0
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["agent_providers"] == [
            ("claude", "anthropic"),
            ("codex", "openai"),
        ]
        assert mock_create.call_args.kwargs["credential_providers"] == [
            "anthropic",
            "openai",
            "vertex",
        ]

    @patch("paude.cli.create_podman.create_podman_session")
    @patch("paude.cli.create._prepare_session_create")
    def test_single_agent_extra_provider_is_allowed(self, mock_prepare, mock_create):
        """An extra credential provider need not map to an agent."""
        mock_prepare.return_value = ([], [], {}, False)
        result = runner.invoke(
            app, ["create", "--agents", "claude", "--providers", "vertex,openai"]
        )
        assert result.exit_code == 0
        mock_create.assert_called_once()
        assert mock_create.call_args.kwargs["credential_providers"] == [
            "vertex",
            "openai",
        ]

    def test_extra_credential_provider_shown_in_dry_run(self):
        """Dry-run shows the exact credential-provider set."""
        result = runner.invoke(
            app,
            [
                "create",
                "--agents",
                "claude,codex",
                "--providers",
                "vertex,chatgpt,openai",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        out = _strip_ansi(result.stdout)
        assert "credential providers: vertex, chatgpt, openai" in out
        assert "codex -> chatgpt" in out


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(
            ["create", "--dry-run", "--github-token", "ghp_test"], id="create"
        ),
        pytest.param(
            ["start", "test-session", "--github-token", "ghp_test"], id="start"
        ),
        pytest.param(
            ["connect", "test-session", "--github-token", "ghp_test"], id="connect"
        ),
    ],
)
def test_no_command_accepts_github_token(args):
    """No command accepts --github-token; PAUDE_GITHUB_TOKEN env var is the only mechanism."""
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert "No such option" in result.output


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestCreateHostFlag:
    """Tests for --host and --ssh-key CLI flags."""

    def test_host_flag_recognized(self):
        """--host flag is accepted by the create command."""
        result = runner.invoke(app, ["create", "--help"])
        assert "--host" in _strip_ansi(result.stdout)

    def test_ssh_key_flag_recognized(self):
        """--ssh-key flag is accepted by the create command."""
        result = runner.invoke(app, ["create", "--help"])
        assert "--ssh-key" in _strip_ansi(result.stdout)

    def test_ssh_key_without_host_rejected(self):
        """--ssh-key requires --host."""
        result = runner.invoke(app, ["create", "--ssh-key", "/path/to/key"])
        output = result.stdout + (result.stderr or "")
        assert result.exit_code == 1
        assert "--ssh-key requires --host" in output

    @patch("paude.transport.ssh.SshTransport")
    def test_host_validates_ssh_connection(self, mock_ssh_class):
        """--host validates SSH connectivity before proceeding."""
        mock_transport = MagicMock()
        mock_transport.validate.side_effect = RuntimeError(
            "SSH connection to badhost failed"
        )
        mock_ssh_class.return_value = mock_transport

        result = runner.invoke(
            app, ["create", "--backend=docker", "--host", "user@badhost"]
        )
        output = result.stdout + (result.stderr or "")
        assert result.exit_code == 1
        assert "SSH connection to badhost failed" in output

    @patch("paude.transport.ssh.SshTransport")
    def test_host_validates_engine_on_remote(self, mock_ssh_class):
        """--host validates that the engine binary exists on the remote."""
        mock_transport = MagicMock()
        mock_transport.validate.return_value = None
        mock_transport.validate_engine.side_effect = RuntimeError(
            "'docker' not found on user@host"
        )
        mock_ssh_class.return_value = mock_transport

        result = runner.invoke(
            app, ["create", "--backend=docker", "--host", "user@host"]
        )
        output = result.stdout + (result.stderr or "")
        assert result.exit_code == 1
        assert "'docker' not found on user@host" in output


def test_bare_paude_shows_list():
    """Bare 'paude' command shows session list with helpful hints."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    # Should show either "No sessions found." or the session list header
    assert "No sessions found." in result.stdout or "NAME" in result.stdout
    # When no sessions, should show helpful next steps
    if "No sessions found." in result.stdout:
        assert "paude create" in result.stdout


def test_help_shows_commands():
    """Help shows commands section."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "create" in result.stdout
    assert "start" in result.stdout
    assert "stop" in result.stdout
    assert "list" in result.stdout


def test_help_shows_extra_sections():
    """Help includes extra reference sections as Rich panels."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "Workflow" in output
    assert "Syncing Code" in output
    assert "Copying Files" in output
    assert "Egress Filtering" in output
    assert "Examples" in output
    assert "Configuration" in output
    assert "Security" in output
    assert "Agents" in output


@pytest.mark.parametrize(
    ("command", "description"),
    [
        pytest.param("stop", "Stop a session", id="stop"),
        pytest.param("list", "List all sessions", id="list"),
        pytest.param("connect", "Attach to a running session", id="connect"),
    ],
)
def test_subcommand_help(command, description):
    """Subcommand --help shows its own help, not main help."""
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert command in result.stdout.lower()
    assert description in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


def test_remote_help():
    """'remote --help' shows subcommand help."""
    result = runner.invoke(app, ["remote", "--help"])
    assert result.exit_code == 0
    assert "remote" in result.stdout.lower()
    assert "git" in result.stdout.lower() or "ACTION" in result.stdout
    assert "paude - Run Claude Code" not in result.stdout


class TestRemoteCommand:
    """Tests for paude remote command."""

    @patch("paude.git_remote.list_paude_remotes")
    def test_remote_list_shows_remotes(self, mock_list):
        """remote list shows all paude git remotes."""
        mock_list.return_value = [
            ("paude-my-session", "ext::podman exec paude-my-session %S /pvc/workspace"),
            ("paude-other", "ext::docker exec paude-other %S /pvc/workspace"),
        ]

        result = runner.invoke(app, ["remote", "list"])

        assert result.exit_code == 0
        assert "paude-my-session" in result.stdout
        assert "paude-other" in result.stdout

    @patch("paude.git_remote.list_paude_remotes")
    def test_remote_list_empty(self, mock_list):
        """remote list shows helpful message when no remotes."""
        mock_list.return_value = []

        result = runner.invoke(app, ["remote", "list"])

        assert result.exit_code == 0
        assert "No paude git remotes found" in result.stdout
        assert "paude remote add" in result.stdout

    @pytest.mark.parametrize(
        "action",
        [
            pytest.param("add", id="add"),
            pytest.param("remove", id="remove"),
            pytest.param("cleanup", id="cleanup"),
        ],
    )
    @patch("paude.git_remote.is_git_repository")
    def test_remote_action_requires_git_repo(self, mock_is_git, action):
        """remote add/remove/cleanup fails if not in git repository."""
        mock_is_git.return_value = False

        result = runner.invoke(app, ["remote", action, "my-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Not a git repository" in output

    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.git_remote_remove")
    def test_remote_remove_success(self, mock_remove, mock_is_git):
        """remote remove successfully removes a remote."""
        mock_is_git.return_value = True
        mock_remove.return_value = True

        result = runner.invoke(app, ["remote", "remove", "my-session"])

        assert result.exit_code == 0
        assert "Removed git remote 'paude-my-session'" in result.stdout
        mock_remove.assert_called_once_with("paude-my-session")

    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.git_remote_remove")
    def test_remote_remove_not_found(self, mock_remove, mock_is_git):
        """remote remove fails when remote doesn't exist."""
        mock_is_git.return_value = True
        mock_remove.return_value = False

        result = runner.invoke(app, ["remote", "remove", "nonexistent"])

        assert result.exit_code == 1

    def test_remote_unknown_action(self):
        """remote with unknown action shows error."""
        result = runner.invoke(app, ["remote", "invalid"])

        assert result.exit_code == 1
        # Error goes to stderr, which typer may redirect to stdout
        output = result.stdout + (result.stderr or "")
        assert "Unknown action: invalid" in output
        assert "Valid actions: add, list, remove, cleanup" in output

    @patch("paude.cli.remote.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    def test_remote_add_fails_when_container_not_running(
        self, mock_running, mock_ext, mock_is_git, mock_find
    ):
        """remote add fails if container is not running."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = False

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "test-session"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Container not running" in output
        assert "paude start test-session" in output

    @patch("paude.cli.remote.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    @patch("paude.git_remote.git_push_to_remote")
    @patch("paude.git_remote.set_base_ref_in_container")
    def test_remote_add_with_push_flag(
        self,
        mock_set_base_ref,
        mock_push,
        mock_branch,
        mock_add,
        mock_init,
        mock_running,
        mock_ext,
        mock_is_git,
        mock_find,
    ):
        """remote add --push adds remote and pushes."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = True
        mock_init.return_value = True
        mock_add.return_value = True
        mock_branch.return_value = "main"
        mock_push.return_value = True
        mock_set_base_ref.return_value = True

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "--push", "test-session"])

        assert result.exit_code == 0
        output = result.stdout + (result.stderr or "")
        assert "Added git remote" in output
        assert "Pushing main to container" in output
        assert "Push complete" in output
        mock_init.assert_called_once()
        mock_push.assert_called_once_with("paude-test-session", "main")

    @patch("paude.cli.remote.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    def test_remote_add_initializes_container_workspace(
        self,
        mock_branch,
        mock_add,
        mock_init,
        mock_running,
        mock_ext,
        mock_is_git,
        mock_find,
    ):
        """remote add initializes git in container before adding remote."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = True
        mock_init.return_value = True
        mock_add.return_value = True
        mock_branch.return_value = "main"

        # Create a mock session
        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"

        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(app, ["remote", "add", "test-session"])

        assert result.exit_code == 0
        output = result.stdout + (result.stderr or "")
        assert "Initializing git repository in container" in output
        mock_init.assert_called_once()

    @patch("paude.cli.remote.find_session_backend")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.git_remote.is_ext_protocol_allowed")
    @patch("paude.git_remote.is_container_running_podman")
    @patch("paude.git_remote.initialize_container_workspace")
    @patch("paude.git_remote.git_remote_add")
    @patch("paude.git_remote.get_current_branch")
    def test_remote_add_container_path_and_remote_name(
        self,
        mock_branch,
        mock_add,
        mock_init,
        mock_running,
        mock_ext,
        mock_is_git,
        mock_find,
    ):
        """--container-path/--remote expose a sub-path under a custom remote name."""
        mock_is_git.return_value = True
        mock_ext.return_value = True
        mock_running.return_value = True
        mock_init.return_value = True
        mock_add.return_value = True
        mock_branch.return_value = "main"

        mock_session = MagicMock()
        mock_session.name = "test-session"
        mock_session.backend_type = "podman"
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = mock_session
        mock_find.return_value = (mock_session, mock_backend)

        result = runner.invoke(
            app,
            [
                "remote",
                "add",
                "test-session",
                "--container-path",
                "/pvc/workspace/rigs/vllm",
                "--remote",
                "rig-vllm",
            ],
        )

        assert result.exit_code == 0
        # container name stays paude-<session>; remote name + path are custom
        mock_add.assert_called_once_with(
            "rig-vllm",
            "ext::podman exec -i paude-test-session %S /pvc/workspace/rigs/vllm",
        )
        assert mock_init.call_args.kwargs["workspace_path"] == (
            "/pvc/workspace/rigs/vllm"
        )


class TestHarvestCommand:
    """Tests for the harvest command CLI wiring."""

    @patch("paude.workflow.harvest_session")
    def test_harvest_passes_new_flags(self, mock_harvest):
        """--container-path/--remote/--repo are forwarded to harvest_session."""
        result = runner.invoke(
            app,
            [
                "harvest",
                "my-session",
                "-b",
                "fix/foo",
                "--container-path",
                "/pvc/workspace/rigs/vllm",
                "--remote",
                "rig-vllm",
                "--repo",
                "/host/vllm",
            ],
        )

        assert result.exit_code == 0
        mock_harvest.assert_called_once_with(
            session_name="my-session",
            branch_name="fix/foo",
            create_pr=False,
            pr_title=None,
            container_path="/pvc/workspace/rigs/vllm",
            remote_name="rig-vllm",
            repo="/host/vllm",
            source_branch=None,
        )

    @patch("paude.workflow.harvest_session")
    def test_harvest_defaults_preserved(self, mock_harvest):
        """Omitting the new flags lets workflow apply single-repo defaults."""
        result = runner.invoke(app, ["harvest", "my-session", "-b", "fix/foo"])

        assert result.exit_code == 0
        _args, kwargs = mock_harvest.call_args
        assert kwargs["container_path"] is None
        assert kwargs["remote_name"] is None
        assert kwargs["repo"] is None

    @patch("paude.workflow.harvest_session")
    def test_harvest_source_defaults_branch(self, mock_harvest):
        result = runner.invoke(app, ["harvest", "my-session", "--from", "feature/foo"])

        assert result.exit_code == 0
        mock_harvest.assert_called_once_with(
            session_name="my-session",
            branch_name=None,
            create_pr=False,
            pr_title=None,
            container_path=None,
            remote_name=None,
            repo=None,
            source_branch="feature/foo",
        )


def test_subcommand_runs_without_main_execution():
    """Subcommands run without triggering main execution logic."""
    # This test verifies that subcommands don't trigger podman checks
    # by confirming they complete without the "podman required" error
    result = runner.invoke(app, ["stop", "--help"])
    assert result.exit_code == 0
    assert "Stop a session" in result.stdout
    assert "podman is required" not in result.stdout


# Tests for connect command multi-backend search behavior


def _make_session(
    name: str,
    status: str = "running",
    workspace: Path | None = None,
    backend_type: str = "podman",
) -> Session:
    """Helper to create a Session object for tests."""
    return Session(
        name=name,
        status=status,
        workspace=workspace or Path("/some/path"),
        created_at="2024-01-15T10:00:00Z",
        backend_type=backend_type,
    )


class TestConnectMultiBackend:
    """Tests for connect command searching multiple backends."""

    @pytest.fixture(autouse=True)
    def _clear_github_token(self, monkeypatch):
        monkeypatch.delenv("PAUDE_GITHUB_TOKEN", raising=False)

    @pytest.fixture(autouse=True)
    def _mock_docker_engine(self):
        """Block Docker backend creation in collect_all_sessions."""
        with patch(
            "paude.session_discovery.ContainerEngine",
            side_effect=Exception("docker not available"),
        ):
            yield


class TestStartMultiBackend:
    """Tests for start command searching multiple backends."""

    @pytest.fixture(autouse=True)
    def _mock_docker_engine(self):
        """Block Docker backend creation in collect_all_sessions."""
        with patch(
            "paude.session_discovery.ContainerEngine",
            side_effect=Exception("docker not available"),
        ):
            yield


class TestStartErrorReporting:
    """Tests for surfacing the real error when a session fails to start."""

    @patch("paude.cli.commands.lifecycle.find_session_backend")
    def test_start_surfaces_podman_stderr(self, mock_find_backend: MagicMock):
        """A CalledProcessError's captured stderr is echoed, not swallowed."""
        mock_backend = MagicMock()
        mock_backend.start_session.side_effect = subprocess.CalledProcessError(
            125, ["podman", "start", "paude-my-session"], "", "no such network"
        )
        mock_find_backend.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["start", "my-session"])

        assert result.exit_code == 1
        assert "no such network" in result.output


class TestStopMultiBackend:
    """Tests for stop command searching multiple backends."""

    @pytest.fixture(autouse=True)
    def _mock_docker_engine(self):
        """Block Docker backend creation in collect_all_sessions."""
        with patch(
            "paude.session_discovery.ContainerEngine",
            side_effect=Exception("docker not available"),
        ):
            yield


class TestDeleteGitRemoteCleanup:
    """Tests for git remote cleanup when deleting sessions."""

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_removes_git_remote(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete calls git remote cleanup after successful session deletion."""
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        mock_cleanup.assert_called_once_with("my-session", Path("/some/project"))

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_works_when_not_in_git_repo(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_subprocess_run: MagicMock,
    ):
        """Delete works when not in a git repository."""
        mock_is_git.return_value = False
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should not show "Removed git remote" since not in git repo
        assert "Removed git remote" not in result.output
        # Should not have called git remote remove since not in git repo
        mock_subprocess_run.assert_not_called()

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_works_when_remote_does_not_exist(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete works when git remote doesn't exist."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1, stderr="error: No such remote: 'paude-my-session'"
        )
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should not print anything about git remote since it didn't exist
        assert "Removed git remote" not in result.output
        assert "Warning" not in result.output
        # Verify correct command was called (cwd=None since workspace is None)
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=None,
        )

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_shows_message_when_remote_removed(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete shows message when git remote is successfully removed."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        assert "Removed git remote 'paude-my-session'." in result.output

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_continues_on_git_remote_failure(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
    ):
        """Delete continues even if git remote removal fails unexpectedly."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1, stderr="fatal: some other error"
        )
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        # Session delete should still succeed
        assert result.exit_code == 0
        assert "Session 'my-session' deleted." in result.output
        # Should show warning about git failure with the error message
        output = result.stdout + (result.stderr or "")
        assert "Warning: Failed to remove git remote: fatal: some other error" in output

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_does_not_cleanup_git_remote_on_failure(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Git remote cleanup is NOT called when session deletion fails."""
        mock_podman = MagicMock()
        mock_podman.delete_session.side_effect = Exception("Deletion failed")
        mock_podman.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 1
        # Cleanup should NOT have been called since deletion failed
        mock_cleanup.assert_not_called()

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.commands.delete.find_session_backend")
    def test_delete_cleans_git_remote_with_auto_detected_backend(
        self,
        mock_find_backend: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete cleans up git remote when backend is auto-detected."""
        mock_backend = MagicMock()
        mock_backend.get_session.return_value = MagicMock(
            workspace=Path("/some/project")
        )
        mock_find_backend.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["delete", "auto-session", "--confirm"])

        assert result.exit_code == 0
        mock_cleanup.assert_called_once_with("auto-session", Path("/some/project"))


class TestDeleteUsesWorkspacePath:
    """Tests for delete using stored workspace path for git remote cleanup."""

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_cleans_remote_from_workspace_dir(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ):
        """Delete removes git remote from stored workspace directory."""
        mock_is_git.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(workspace=tmp_path)
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        assert "Removed git remote 'paude-my-session'." in result.output
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )

    @patch("paude.cli.remote.subprocess.run")
    @patch("paude.git_remote.is_git_repository")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_falls_back_to_cwd_when_workspace_not_git(
        self,
        mock_podman_class: MagicMock,
        mock_is_git: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ):
        """Delete falls back to current dir when workspace is not a git repo."""
        # Workspace is not a git repo, current dir is
        mock_is_git.side_effect = lambda cwd=None: cwd is None
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = MagicMock(workspace=tmp_path)
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        mock_run.assert_called_once_with(
            ["git", "remote", "remove", "paude-my-session"],
            capture_output=True,
            text=True,
            cwd=None,
        )

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.cli.helpers.PodmanBackend")
    def test_delete_passes_none_workspace_when_session_not_found(
        self,
        mock_podman_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Delete passes None workspace when get_session returns None."""
        mock_podman = MagicMock()
        mock_podman.get_session.return_value = None
        mock_podman_class.return_value = mock_podman

        result = runner.invoke(
            app, ["delete", "my-session", "--confirm", "--backend=podman"]
        )

        assert result.exit_code == 0
        mock_cleanup.assert_called_once_with("my-session", None)


class TestDeleteForce:
    """Tests for --force flag on delete command."""

    @patch("paude.cli.remote._cleanup_session_git_remote")
    @patch("paude.registry.SessionRegistry")
    def test_force_delete_removes_from_registry(
        self,
        mock_registry_class: MagicMock,
        mock_cleanup: MagicMock,
    ):
        """Force delete removes an orphaned session from the registry."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = MagicMock(
            workspace="/some/project",
            ssh_host=None,
            remote_config_dir=None,
        )
        mock_registry.unregister.return_value = True
        mock_registry_class.return_value = mock_registry

        result = runner.invoke(app, ["delete", "orphan", "--confirm", "--force"])

        assert result.exit_code == 0
        assert "removed from local config" in result.output
        mock_registry.unregister.assert_called_once_with("orphan")
        mock_cleanup.assert_called_once_with("orphan", Path("/some/project"))

    @patch("paude.registry.SessionRegistry")
    def test_force_delete_not_found(
        self,
        mock_registry_class: MagicMock,
    ):
        """Force delete exits with error when session not in registry."""
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        mock_registry.unregister.return_value = False
        mock_registry_class.return_value = mock_registry

        result = runner.invoke(app, ["delete", "ghost", "--confirm", "--force"])

        assert result.exit_code == 1
        assert "not found in local config" in result.output

    def test_force_delete_requires_confirm(self):
        """Force delete still requires --confirm."""
        result = runner.invoke(app, ["delete", "orphan", "--force"])

        assert result.exit_code == 1
        assert "Use --confirm to proceed" in result.output


class TestRemoteCleanup:
    """Tests for paude remote cleanup command."""

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_removes_orphaned_remotes(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup removes remotes for sessions that no longer exist."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-active", "ext::podman exec paude-active %S /pvc/workspace"),
            ("paude-orphan", "ext::podman exec paude-orphan %S /pvc/workspace"),
        ]
        active_session = MagicMock()
        active_session.name = "active"
        mock_collect.return_value = ([(active_session, MagicMock())], {"podman"})

        with patch("paude.git_remote.git_remote_remove", return_value=True) as mock_rm:
            result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        mock_rm.assert_called_once_with("paude-orphan")
        assert "Removed orphaned remote 'paude-orphan'" in result.stdout
        assert "Removed 1 orphaned remote(s)." in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_no_orphans(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup reports when no orphaned remotes found."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-active", "ext::podman exec paude-active %S /pvc/workspace"),
        ]
        active_session = MagicMock()
        active_session.name = "active"
        mock_collect.return_value = ([(active_session, MagicMock())], {"podman"})

        result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert "No orphaned remotes found." in result.stdout

    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_no_remotes(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
    ):
        """Cleanup reports when no paude remotes exist."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = []

        result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert "No paude git remotes found." in result.stdout

    @patch("paude.session_discovery.collect_all_sessions")
    @patch("paude.git_remote.list_paude_remotes")
    @patch("paude.git_remote.is_git_repository")
    def test_cleanup_removes_multiple_orphans(
        self,
        mock_is_git: MagicMock,
        mock_list_remotes: MagicMock,
        mock_collect: MagicMock,
    ):
        """Cleanup removes multiple orphaned remotes."""
        mock_is_git.return_value = True
        mock_list_remotes.return_value = [
            ("paude-gone1", "ext::podman exec paude-gone1 %S /pvc/workspace"),
            ("paude-gone2", "ext::podman exec paude-gone2 %S /pvc/workspace"),
        ]
        mock_collect.return_value = ([], set())

        with patch("paude.git_remote.git_remote_remove", return_value=True) as mock_rm:
            result = runner.invoke(app, ["remote", "cleanup"])

        assert result.exit_code == 0
        assert mock_rm.call_count == 2
        assert "Removed 2 orphaned remote(s)." in result.stdout


class TestParseCopyPath:
    """Tests for _parse_copy_path helper."""

    @pytest.mark.parametrize(
        ("input_path", "expected"),
        [
            pytest.param(
                "/absolute/path", (None, "/absolute/path"), id="absolute-local"
            ),
            pytest.param(
                "./relative/path", (None, "./relative/path"), id="relative-local"
            ),
            pytest.param("file.txt", (None, "file.txt"), id="bare-filename"),
            pytest.param(
                "../parent/file.txt", (None, "../parent/file.txt"), id="parent-relative"
            ),
            pytest.param(
                "my-session:file.txt",
                ("my-session", "file.txt"),
                id="session-with-path",
            ),
            pytest.param(
                "my-session:/abs/path",
                ("my-session", "/abs/path"),
                id="session-absolute",
            ),
            pytest.param(":file.txt", ("", "file.txt"), id="auto-detect-session"),
        ],
    )
    def test_parse_copy_path(self, input_path, expected):
        """_parse_copy_path correctly parses various path formats."""
        assert _parse_copy_path(input_path) == expected


class TestCpCommand:
    """Tests for paude cp command."""

    def test_cp_no_remote_path_errors(self):
        """Both paths local should error."""
        result = runner.invoke(app, ["cp", "./file.txt", "./dest.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "One of SRC or DEST must be a remote path" in output

    def test_cp_both_remote_errors(self):
        """Both paths remote should error."""
        result = runner.invoke(app, ["cp", "sess1:file.txt", "sess2:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "Only one of SRC or DEST can be a remote path" in output

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_to_session_calls_copy_to(self, mock_find):
        """cp local -> session calls copy_to_session."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./file.txt", "/pvc/workspace/file.txt"
        )

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_from_session_calls_copy_from(self, mock_find):
        """cp session -> local calls copy_from_session."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "my-session:output.log", "./"])

        assert result.exit_code == 0
        mock_backend.copy_from_session.assert_called_once_with(
            "my-session", "/pvc/workspace/output.log", "./"
        )

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_relative_remote_path_resolved(self, mock_find):
        """Relative remote paths get /pvc/workspace/ prefix."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./local", "my-session:subdir/file"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./local", "/pvc/workspace/subdir/file"
        )

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_absolute_remote_path_preserved(self, mock_find):
        """Absolute remote paths are used as-is."""
        mock_backend = MagicMock()
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./local", "my-session:/tmp/file"])

        assert result.exit_code == 0
        mock_backend.copy_to_session.assert_called_once_with(
            "my-session", "./local", "/tmp/file"
        )

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_session_not_found(self, mock_find):
        """Error when session doesn't exist."""
        mock_find.return_value = None

        result = runner.invoke(app, ["cp", "./file.txt", "nonexistent:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "not found" in output

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_copy_failure_shows_error(self, mock_find):
        """Backend raises, CLI shows error."""
        mock_backend = MagicMock()
        mock_backend.copy_to_session.side_effect = Exception("copy failed")
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "copy failed" in output

    @patch("paude.cli.commands.cp.find_session_backend")
    def test_cp_session_not_running_shows_error(self, mock_find):
        """ValueError from backend shows error."""
        mock_backend = MagicMock()
        mock_backend.copy_to_session.side_effect = ValueError(
            "Session 'my-session' is not running."
        )
        mock_find.return_value = ("podman", mock_backend)

        result = runner.invoke(app, ["cp", "./file.txt", "my-session:file.txt"])

        assert result.exit_code == 1
        output = result.stdout + (result.stderr or "")
        assert "not running" in output

    def test_cp_help(self):
        """'cp --help' shows subcommand help."""
        result = runner.invoke(app, ["cp", "--help"])

        assert result.exit_code == 0
        assert "Copy files between local and a session" in result.stdout

    def test_help_shows_cp_command(self):
        """Main help shows cp command."""
        result = runner.invoke(app, ["--help"])

        assert result.exit_code == 0
        assert "cp" in result.stdout


# ---------------------------------------------------------------------------
# blocked-domains subcommand
# ---------------------------------------------------------------------------


class TestBlockedDomainsCLI:
    """Tests for the blocked-domains CLI subcommand."""

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_no_proxy_message(self, mock_resolve: MagicMock) -> None:
        """Shows message when session was created without a proxy."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = None
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "created without a proxy" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_no_blocked_domains_message(self, mock_resolve: MagicMock) -> None:
        """Shows no-blocked message when log is empty."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = ""
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "No blocked domains" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_raw_output(self, mock_resolve: MagicMock) -> None:
        """--raw dumps raw log content."""
        log = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = log
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session", "--raw"])
        assert result.exit_code == 0
        assert "evil.com:443" in result.stdout
        assert "BLOCKED" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_parsed_summary_output(self, mock_resolve: MagicMock) -> None:
        """Default output shows parsed summary."""
        log = (
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:01:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:02:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT other.com:443 BLOCKED\n"
        )
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.return_value = log
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "my-session"])
        assert result.exit_code == 0
        assert "evil.com" in result.stdout
        assert "other.com" in result.stdout
        assert "2 unique domain(s) blocked (3 total requests)" in result.stdout
        assert "paude allowed-domains my-session --add" in result.stdout

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_session_not_found_error(self, mock_resolve: MagicMock) -> None:
        """Shows error when session not found."""
        from paude.backends.podman import SessionNotFoundError

        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.side_effect = SessionNotFoundError(
            "Session 'nope' not found"
        )
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "nope"])
        assert result.exit_code == 1

    @patch("paude.cli.domains._resolve_backend_for_domains")
    def test_proxy_not_running_error(self, mock_resolve: MagicMock) -> None:
        """Shows error when proxy not running."""
        mock_backend = MagicMock()
        mock_backend.get_proxy_blocked_log.side_effect = ValueError(
            "Proxy for session 'x' is not running."
        )
        mock_resolve.return_value = mock_backend

        result = runner.invoke(app, ["blocked-domains", "x"])
        assert result.exit_code == 1


def test_help_includes_blocked_domains() -> None:
    """Help output includes blocked-domains command."""
    result = runner.invoke(app, ["--help"])
    assert "blocked-domains" in result.stdout


class TestDetectDevScriptDir:
    """Tests for _detect_dev_script_dir()."""

    def test_returns_project_root_in_src_layout(self, tmp_path: Path) -> None:
        """Returns project root when containers/paude/Dockerfile exists (src layout)."""
        from paude.cli.helpers import _detect_dev_script_dir

        (tmp_path / "containers" / "paude").mkdir(parents=True)
        (tmp_path / "containers" / "paude" / "Dockerfile").touch()

        # Simulate src layout: project_root/src/paude/cli/helpers.py (4 levels)
        fake_file = tmp_path / "src" / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result == tmp_path

    def test_returns_project_root_in_flat_layout(self, tmp_path: Path) -> None:
        """Returns project root when containers/paude/Dockerfile exists (flat layout)."""
        from paude.cli.helpers import _detect_dev_script_dir

        (tmp_path / "containers" / "paude").mkdir(parents=True)
        (tmp_path / "containers" / "paude" / "Dockerfile").touch()

        # Simulate flat layout: project_root/paude/cli/helpers.py (3 levels)
        fake_file = tmp_path / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result == tmp_path

    def test_returns_none_when_no_dockerfile(self, tmp_path: Path) -> None:
        """Returns None when no containers/paude/Dockerfile found."""
        from paude.cli.helpers import _detect_dev_script_dir

        # No Dockerfile anywhere
        fake_file = tmp_path / "src" / "paude" / "cli" / "helpers.py"

        with patch("paude.cli.helpers.__file__", str(fake_file)):
            result = _detect_dev_script_dir()
        assert result is None


class TestRunSetupCommand:
    """Tests for _run_setup_command."""

    def test_runs_command_in_workspace(self):
        """Executes the command with cd to /pvc/workspace."""
        from paude.cli.helpers import _run_setup_command

        backend = MagicMock()
        backend.exec_in_session.return_value = (0, "ok\n", "")

        _run_setup_command(backend, "test-session", "npm install")

        backend.exec_in_session.assert_called_once_with(
            "test-session", "cd /pvc/workspace && npm install"
        )

    def test_prints_stdout_and_stderr(self, capsys):
        """Prints command output to stderr."""
        from paude.cli.helpers import _run_setup_command

        backend = MagicMock()
        backend.exec_in_session.return_value = (
            0,
            "installed 42 packages\n",
            "warn: deprecated\n",
        )

        _run_setup_command(backend, "s1", "npm install")

        captured = capsys.readouterr()
        assert "installed 42 packages" in captured.err
        assert "warn: deprecated" in captured.err
        assert "Setup command completed" in captured.err

    def test_warns_on_failure(self, capsys):
        """Prints warning when command fails."""
        from paude.cli.helpers import _run_setup_command

        backend = MagicMock()
        backend.exec_in_session.return_value = (1, "", "error: not found\n")

        _run_setup_command(backend, "s1", "bad-cmd")

        captured = capsys.readouterr()
        assert "setup command failed (exit 1)" in captured.err


class TestPrepareSessionCreateGitIdentity:
    """Git identity is resolved and injected during pre-create."""

    @patch(
        "paude.git_remote.resolve_local_git_identity",
        return_value=("Ada Lovelace", "ada@example.com"),
    )
    def test_injects_identity_env(self, mock_resolve):
        """Resolved identity is passed to the container as env vars."""
        from paude.cli.helpers import _prepare_session_create

        _domains, _args, env, _unrestricted = _prepare_session_create(
            allowed_domains=None,
            yolo=False,
            claude_args=None,
            config_obj=None,
        )

        assert env["PAUDE_GIT_USER_NAME"] == "Ada Lovelace"
        assert env["PAUDE_GIT_USER_EMAIL"] == "ada@example.com"

    @patch(
        "paude.git_remote.resolve_local_git_identity",
        return_value=(None, None),
    )
    def test_warns_and_omits_when_no_identity(self, mock_resolve, capsys):
        """No identity: env vars are omitted and a warning is printed."""
        from paude.cli.helpers import _prepare_session_create

        _domains, _args, env, _unrestricted = _prepare_session_create(
            allowed_domains=None,
            yolo=False,
            claude_args=None,
            config_obj=None,
        )

        assert "PAUDE_GIT_USER_NAME" not in env
        assert "PAUDE_GIT_USER_EMAIL" not in env
        captured = capsys.readouterr()
        assert "No git identity found" in captured.err

    @patch(
        "paude.git_remote.resolve_local_git_identity",
        return_value=("Ada Lovelace", None),
    )
    def test_partial_identity_sets_only_known_field(self, mock_resolve, capsys):
        """A name with no email sets only the name and does not warn."""
        from paude.cli.helpers import _prepare_session_create

        _domains, _args, env, _unrestricted = _prepare_session_create(
            allowed_domains=None,
            yolo=False,
            claude_args=None,
            config_obj=None,
        )

        assert env["PAUDE_GIT_USER_NAME"] == "Ada Lovelace"
        assert "PAUDE_GIT_USER_EMAIL" not in env
        captured = capsys.readouterr()
        assert "No git identity found" not in captured.err
