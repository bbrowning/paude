"""Tests for podman secret management for proxy credentials."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from paude.agents.cursor import CursorAgent
from paude.backends.podman.helpers import proxy_secret_name, proxy_secret_prefix
from paude.backends.podman.proxy_credentials import ProxyCredentialManager
from paude.backends.proxy_config import (
    ProxyCredentials,
    proxy_credential_targets,
    required_proxy_credential_targets,
)
from paude.container.engine import ContainerEngine
from paude.container.proxy_inspect import ProxyInspectionError
from paude.container.proxy_runner import ProxyRunner, ProxyStartError
from paude.container.runner import ContainerRunner
from tests.fakes import FakeTransport, make_engine, recorded_commands


class TestProxySecretName:
    """Tests for proxy_secret_name and proxy_secret_prefix helpers."""

    def test_basic_naming(self) -> None:
        result = proxy_secret_name("my-session", "ANTHROPIC_API_KEY")
        assert result == "paude-proxy-cred-my-session-anthropic-api-key"

    def test_gh_token(self) -> None:
        result = proxy_secret_name("sess-1", "GH_TOKEN")
        assert result == "paude-proxy-cred-sess-1-gh-token"

    def test_gcp_adc(self) -> None:
        result = proxy_secret_name("sess", "GCP_ADC_JSON")
        assert result == "paude-proxy-cred-sess-gcp-adc-json"

    def test_name_starts_with_prefix(self) -> None:
        """Secret names must start with the session prefix."""
        name = proxy_secret_name("sess", "API_KEY")
        assert name.startswith(proxy_secret_prefix("sess"))

    def test_prefix_format(self) -> None:
        assert proxy_secret_prefix("my-sess") == "paude-proxy-cred-my-sess-"


class TestCreateSecretFromValue:
    """Tests for ContainerRunner.create_secret_from_value."""

    @patch("subprocess.run")
    def test_creates_secret_via_stdin(self, mock_run: MagicMock) -> None:
        """Podman creates secret by piping value through stdin."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_secret_from_value("my-secret", "secret-value")

        # Should call: podman secret rm (cleanup), then podman secret create
        calls = mock_run.call_args_list
        rm_call = [c for c in calls if "secret" in c[0][0] and "rm" in c[0][0]]
        create_call = [c for c in calls if "secret" in c[0][0] and "create" in c[0][0]]
        assert len(rm_call) == 1
        assert len(create_call) == 1

        # Verify create uses stdin (-)
        cmd = create_call[0][0][0]
        assert cmd == ["podman", "secret", "create", "my-secret", "-"]
        assert create_call[0][1].get("input") == "secret-value"

    @patch("subprocess.run")
    def test_removes_existing_secret_first(self, mock_run: MagicMock) -> None:
        """create_secret_from_value removes any existing secret first."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_secret_from_value("my-secret", "val")

        calls = mock_run.call_args_list
        # rm should be called before create
        rm_idx = next(
            i for i, c in enumerate(calls) if "rm" in c[0][0] and "secret" in c[0][0]
        )
        create_idx = next(
            i
            for i, c in enumerate(calls)
            if "create" in c[0][0] and "secret" in c[0][0]
        )
        assert rm_idx < create_idx


class TestListSecretsByPrefix:
    """Tests for ContainerRunner.list_secrets_by_prefix."""

    @patch("subprocess.run")
    def test_filters_by_prefix(self, mock_run: MagicMock) -> None:
        """list_secrets_by_prefix filters secret names by prefix."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "paude-proxy-cred-sess1-api-key\n"
                "paude-proxy-cred-sess1-gh-token\n"
                "paude-proxy-cred-sess2-api-key\n"
                "other-secret\n"
            ),
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        result = runner.list_secrets_by_prefix("paude-proxy-cred-sess1-")

        assert result == [
            "paude-proxy-cred-sess1-api-key",
            "paude-proxy-cred-sess1-gh-token",
        ]

    @patch("subprocess.run")
    def test_returns_empty_when_no_match(self, mock_run: MagicMock) -> None:
        """list_secrets_by_prefix returns empty list when no secrets match."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="other-secret\n",
        )
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        result = runner.list_secrets_by_prefix("paude-proxy-cred-sess-")
        assert result == []

    @patch("subprocess.run")
    def test_returns_empty_on_failure(self, mock_run: MagicMock) -> None:
        """list_secrets_by_prefix returns empty list on command failure."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        result = runner.list_secrets_by_prefix("paude-")
        assert result == []


class TestProxyRunnerSecretArgs:
    """Tests for ProxyRunner._build_secret_args."""

    def test_build_secret_args_with_refs(self) -> None:
        result = ProxyRunner._build_secret_args(
            ["mysecret,type=env,target=MY_VAR", "other,type=env,target=OTHER"]
        )
        assert result == [
            "--secret",
            "mysecret,type=env,target=MY_VAR",
            "--secret",
            "other,type=env,target=OTHER",
        ]

    def test_build_secret_args_empty(self) -> None:
        assert ProxyRunner._build_secret_args(None) == []
        assert ProxyRunner._build_secret_args([]) == []

    @patch("subprocess.run")
    def test_create_session_proxy_with_secrets_excludes_creds_from_env(
        self, mock_run: MagicMock
    ) -> None:
        """When secret_refs are provided, credentials are not passed as -e."""
        mock_run.return_value = MagicMock(returncode=0, stdout="id123", stderr="")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)

        proxy.create_session_proxy(
            name="paude-proxy-test",
            image="proxy:latest",
            network="test-net",
            credentials={"API_KEY": "secret-value"},
            secret_refs=["mysecret,type=env,target=API_KEY"],
        )

        # Find the create call
        create_calls = [c for c in mock_run.call_args_list if "create" in c[0][0]]
        assert create_calls
        call_args = create_calls[0][0][0]

        # --secret should be present
        assert "--secret" in call_args
        secret_idx = call_args.index("--secret")
        assert call_args[secret_idx + 1] == "mysecret,type=env,target=API_KEY"

        # -e API_KEY=secret-value should NOT be present
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert not any("API_KEY" in v for v in env_vals)

    @patch("subprocess.run")
    def test_chatgpt_state_env_passed_without_secret_contents(
        self, mock_run: MagicMock
    ) -> None:
        """The ChatGPT OAuth state-file env var is a plain -e flag, not a secret."""
        mock_run.return_value = MagicMock(returncode=0, stdout="id123", stderr="")
        proxy = ProxyRunner(ContainerRunner(ContainerEngine("podman")))

        proxy.create_session_proxy(
            name="paude-proxy-test",
            image="proxy:latest",
            network="test-net",
            credentials={"OPENAI_API_KEY": "secret-value"},
            secret_refs=["api,type=env,target=OPENAI_API_KEY"],
            credential_env={
                "PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE": "/data/auth/chatgpt-auth.json",
            },
            auth_volume="auth-volume",
        )

        create_call = next(c for c in mock_run.call_args_list if "create" in c[0][0])
        args = create_call[0][0]
        env_values = [args[i + 1] for i, value in enumerate(args) if value == "-e"]
        assert (
            "PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE=/data/auth/chatgpt-auth.json"
            in env_values
        )
        assert not any("secret-value" in value for value in args)
        assert "auth-volume:/data/auth" in args

    @patch("subprocess.run")
    def test_create_session_proxy_without_secrets_uses_env(
        self, mock_run: MagicMock
    ) -> None:
        """Without secret_refs, credentials are passed as -e flags (Docker)."""
        mock_run.return_value = MagicMock(returncode=0, stdout="id123", stderr="")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)

        proxy.create_session_proxy(
            name="paude-proxy-test",
            image="proxy:latest",
            network="test-net",
            credentials={"API_KEY": "secret-value"},
        )

        create_calls = [c for c in mock_run.call_args_list if "create" in c[0][0]]
        assert create_calls
        call_args = create_calls[0][0][0]

        # -e API_KEY=secret-value should be present
        env_indices = [i for i, a in enumerate(call_args) if a == "-e"]
        env_vals = [call_args[i + 1] for i in env_indices]
        assert "API_KEY=secret-value" in env_vals

        # --secret should NOT be present
        assert "--secret" not in call_args


class TestProxyManagerCredentialSecrets:
    """Tests for PodmanProxyManager credential secret lifecycle."""

    def _make_mock_runner(self, engine: str = "podman") -> MagicMock:
        mock = MagicMock()
        mock.engine.binary = engine
        mock.engine.is_podman = engine != "docker"
        mock.engine.supports_secrets = engine != "docker"
        mock.engine.supports_multi_network_create = engine != "docker"
        mock.engine.default_bridge_network = (
            "podman" if engine == "podman" else "bridge"
        )
        mock.engine.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock.list_secrets_by_prefix.return_value = []
        return mock

    def test_create_credential_secrets_podman(self) -> None:
        """Creates podman secrets and returns secret_refs for podman."""
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner("podman")
        manager = PodmanProxyManager(runner, MagicMock())
        refs = manager._create_credential_secrets(
            "sess", {"API_KEY": "val1", "GH_TOKEN": "val2"}
        )

        assert len(refs) == 2
        assert "paude-proxy-cred-sess-api-key,type=env,target=API_KEY" in refs
        assert "paude-proxy-cred-sess-gh-token,type=env,target=GH_TOKEN" in refs
        assert runner.create_secret_from_value.call_count == 2

    @pytest.mark.parametrize("engine", ["podman", "docker"])
    def test_credential_env_sets_chatgpt_state_path_when_flagged(
        self, engine: str
    ) -> None:
        """chatgpt_oauth_mode signals the proxy to activate ChatGPT OAuth support.

        This is a plain env var, so it works identically on Podman and Docker.
        """
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner(engine)
        manager = PodmanProxyManager(runner, MagicMock())

        env = manager._credential_env(ProxyCredentials(chatgpt_oauth_mode=True))

        assert env == {
            "PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE": "/data/auth/chatgpt-auth.json"
        }

    def test_credential_env_empty_when_not_chatgpt_mode(self) -> None:
        """No ChatGPT env var is set for non-ChatGPT sessions."""
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner("podman")
        manager = PodmanProxyManager(runner, MagicMock())

        assert manager._credential_env(ProxyCredentials()) == {}

    def test_create_credential_secrets_docker_noop(self) -> None:
        """Returns empty list for Docker (no secret support)."""
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner("docker")
        manager = PodmanProxyManager(runner, MagicMock())
        refs = manager._create_credential_secrets("sess", {"API_KEY": "val1"})

        assert refs == []
        runner.create_secret_from_value.assert_not_called()

    def test_create_credential_secrets_none_credentials(self) -> None:
        """Returns empty list when credentials is None."""
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner("podman")
        manager = PodmanProxyManager(runner, MagicMock())
        refs = manager._create_credential_secrets("sess", None)

        assert refs == []
        runner.create_secret_from_value.assert_not_called()

    def test_remove_credential_secrets(self) -> None:
        """Removes all secrets matching the session prefix."""
        from paude.backends.podman.proxy import PodmanProxyManager

        runner = self._make_mock_runner("podman")
        runner.list_secrets_by_prefix.return_value = [
            "paude-proxy-cred-sess-api-key",
            "paude-proxy-cred-sess-gh-token",
        ]
        manager = PodmanProxyManager(runner, MagicMock())
        manager.remove_credential_secrets("sess")

        runner.list_secrets_by_prefix.assert_called_once_with("paude-proxy-cred-sess-")
        assert runner.remove_secret.call_count == 2
        runner.remove_secret.assert_any_call("paude-proxy-cred-sess-api-key")
        runner.remove_secret.assert_any_call("paude-proxy-cred-sess-gh-token")


def _result(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestCredentialPreservingUpdates:
    """Credential update preparation reads the live binding, not ambient state."""

    def test_podman_preserves_exact_attached_refs_without_rewriting(self) -> None:
        command = [
            "podman",
            "create",
            "--secret",
            "paude-proxy-cred-sess-claude-code-oauth-token,"
            "type=env,target=CLAUDE_CODE_OAUTH_TOKEN",
            "--secret",
            "old-gh,type=env,target=GH_TOKEN",
        ]
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(command))}
        )
        runner = ContainerRunner(make_engine("podman", transport=transport))

        prepared = ProxyCredentialManager(runner).prepare_update(
            "sess",
            "paude-proxy-sess",
            ProxyCredentials(),
            {"CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"},
            {"CLAUDE_CODE_OAUTH_TOKEN"},
        )

        assert prepared.secret_refs == [
            "paude-proxy-cred-sess-claude-code-oauth-token,"
            "type=env,target=CLAUDE_CODE_OAUTH_TOKEN",
            "old-gh,type=env,target=GH_TOKEN",
        ]
        commands = recorded_commands(runner.engine)
        assert not any(
            command[1:3] in (["secret", "create"], ["secret", "rm"])
            for command in commands
        )

    def test_podman_refresh_stages_new_generation_and_retains_unrelated(self) -> None:
        command = [
            "podman",
            "create",
            "--secret",
            "paude-proxy-cred-sess-claude-code-oauth-token,"
            "type=env,target=CLAUDE_CODE_OAUTH_TOKEN",
            "--secret",
            "old-gh,type=env,target=GH_TOKEN",
        ]
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(command))}
        )
        runner = ContainerRunner(make_engine("podman", transport=transport))
        manager = ProxyCredentialManager(runner)

        prepared = manager.prepare_update(
            "sess",
            "paude-proxy-sess",
            ProxyCredentials(environment={"CLAUDE_CODE_OAUTH_TOKEN": "fresh"}),
            {"CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"},
            {"CLAUDE_CODE_OAUTH_TOKEN"},
        )

        assert "old-gh,type=env,target=GH_TOKEN" in prepared.secret_refs
        assert (
            "paude-proxy-cred-sess-claude-code-oauth-token,"
            "type=env,target=CLAUDE_CODE_OAUTH_TOKEN" not in prepared.secret_refs
        )
        assert len(prepared.staged_secrets) == 1
        assert any(
            ref
            == f"{prepared.staged_secrets[0]},type=env,target=CLAUDE_CODE_OAUTH_TOKEN"
            for ref in prepared.secret_refs
        )
        assert (
            "paude-proxy-cred-sess-claude-code-oauth-token"
            in prepared.superseded_secrets
        )
        manager.commit_update(prepared)
        assert [
            "podman",
            "secret",
            "rm",
            "paude-proxy-cred-sess-claude-code-oauth-token",
        ] in recorded_commands(runner.engine)

    def test_missing_required_binding_fails_before_any_mutation(self) -> None:
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(["podman", "create"]))}
        )
        runner = ContainerRunner(make_engine("podman", transport=transport))

        with pytest.raises(ValueError, match="CLAUDE_CODE_OAUTH_TOKEN"):
            ProxyCredentialManager(runner).prepare_update(
                "sess",
                "paude-proxy-sess",
                ProxyCredentials(),
                {"CLAUDE_CODE_OAUTH_TOKEN"},
                {"CLAUDE_CODE_OAUTH_TOKEN"},
            )

        assert len(recorded_commands(runner.engine)) == 1

    def test_inspect_failure_is_not_treated_as_no_credentials(self) -> None:
        transport = FakeTransport(
            results={"inspect -f": _result(returncode=1, stderr="inspect failed")}
        )
        runner = ContainerRunner(make_engine("podman", transport=transport))

        with pytest.raises(ProxyInspectionError, match="inspect failed"):
            ProxyCredentialManager(runner).prepare_update(
                "sess",
                "paude-proxy-sess",
                ProxyCredentials(),
                {"CLAUDE_CODE_OAUTH_TOKEN"},
                {"CLAUDE_CODE_OAUTH_TOKEN"},
            )

        assert len(recorded_commands(runner.engine)) == 1

    def test_docker_preserves_current_values_and_overlays_only_refresh(self) -> None:
        current = [
            "CLAUDE_CODE_OAUTH_TOKEN=old",
            "GH_TOKEN=unrelated",
            "ALLOWED_DOMAINS=.old.example",
        ]
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(current))}
        )
        runner = ContainerRunner(make_engine("docker", transport=transport))

        prepared = ProxyCredentialManager(runner).prepare_update(
            "sess",
            "paude-proxy-sess",
            ProxyCredentials(environment={"CLAUDE_CODE_OAUTH_TOKEN": "fresh"}),
            {"CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN"},
            {"CLAUDE_CODE_OAUTH_TOKEN"},
        )

        assert prepared.credentials.environment == {
            "CLAUDE_CODE_OAUTH_TOKEN": "fresh",
            "GH_TOKEN": "unrelated",
        }
        assert "ALLOWED_DOMAINS" not in prepared.credentials.environment

    def test_podman_cursor_browser_auth_preserves_unrelated_binding(self) -> None:
        command = [
            "podman",
            "create",
            "--secret",
            "old-gh,type=env,target=GH_TOKEN",
        ]
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(command))}
        )
        runner = ContainerRunner(make_engine("podman", transport=transport))
        cursor = CursorAgent().config
        required = required_proxy_credential_targets(cursor, ["cursor"])

        prepared = ProxyCredentialManager(runner).prepare_update(
            "sess",
            "paude-proxy-sess",
            ProxyCredentials(),
            proxy_credential_targets(cursor),
            required,
        )

        assert required == set()
        assert prepared.secret_refs == ["old-gh,type=env,target=GH_TOKEN"]

    def test_docker_cursor_browser_auth_preserves_unrelated_binding(self) -> None:
        current = ["GH_TOKEN=unrelated", "ALLOWED_DOMAINS=.old.example"]
        transport = FakeTransport(
            results={"inspect -f": _result(stdout=json.dumps(current))}
        )
        runner = ContainerRunner(make_engine("docker", transport=transport))
        cursor = CursorAgent().config
        required = required_proxy_credential_targets(cursor, ["cursor"])

        prepared = ProxyCredentialManager(runner).prepare_update(
            "sess",
            "paude-proxy-sess",
            ProxyCredentials(),
            proxy_credential_targets(cursor),
            required,
        )

        assert required == set()
        assert prepared.credentials.environment == {"GH_TOKEN": "unrelated"}


class TestRollbackProxySwap:
    """A candidate failure restores the retained proxy and its fixed address."""

    def test_start_failure_restores_old_name_network_and_running_state(self) -> None:
        engine = MagicMock()
        engine.supports_multi_network_create = True
        engine.default_bridge_network = "podman"
        starts = 0

        def run(*args: str, **_kwargs: object) -> MagicMock:
            nonlocal starts
            if args == ("start", "paude-proxy-sess"):
                starts += 1
                if starts == 1:
                    return MagicMock(returncode=1, stdout="", stderr="boom")
            return MagicMock(returncode=0, stdout="", stderr="")

        engine.run.side_effect = run
        runner = MagicMock(spec=ContainerRunner)
        runner.engine = engine
        runner.container_running.return_value = True

        with pytest.raises(ProxyStartError, match="Failed to start proxy"):
            ProxyRunner(runner).swap_session_proxy(
                name="paude-proxy-sess",
                image="proxy:latest",
                network="paude-net-sess",
                ip="10.89.0.2",
            )

        commands = [call.args for call in engine.run.call_args_list]
        rename_out = next(i for i, cmd in enumerate(commands) if cmd[0] == "rename")
        disconnect = next(
            i for i, cmd in enumerate(commands) if cmd[:2] == ("network", "disconnect")
        )
        candidate_create = next(
            i for i, cmd in enumerate(commands) if cmd[0] == "create"
        )
        candidate_start = next(
            i for i, cmd in enumerate(commands) if cmd == ("start", "paude-proxy-sess")
        )
        candidate_remove = next(
            i for i, cmd in enumerate(commands) if cmd[:2] == ("rm", "-f")
        )
        reconnect = next(
            i for i, cmd in enumerate(commands) if cmd[:2] == ("network", "connect")
        )
        rename_back = max(i for i, cmd in enumerate(commands) if cmd[0] == "rename")
        restart = max(
            i for i, cmd in enumerate(commands) if cmd == ("start", "paude-proxy-sess")
        )
        assert (
            rename_out
            < disconnect
            < candidate_create
            < candidate_start
            < candidate_remove
            < reconnect
            < rename_back
            < restart
        )
        assert commands[reconnect] == (
            "network",
            "connect",
            "--ip",
            "10.89.0.2",
            "paude-net-sess",
            commands[rename_out][2],
        )

    def test_candidate_exit_after_successful_start_restores_old_proxy(self) -> None:
        engine = MagicMock()
        engine.supports_multi_network_create = True
        engine.default_bridge_network = "podman"

        def run(*args: str, **_kwargs: object) -> MagicMock:
            if args[:3] == ("inspect", "-f", "{{.State.Running}}"):
                return MagicMock(returncode=0, stdout="false\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        engine.run.side_effect = run
        runner = MagicMock(spec=ContainerRunner)
        runner.engine = engine
        runner.container_running.return_value = True

        with pytest.raises(ProxyStartError, match="exited during initialization"):
            ProxyRunner(runner).swap_session_proxy(
                name="paude-proxy-sess",
                image="proxy:latest",
                network="paude-net-sess",
                ip="10.89.0.2",
            )

        commands = [call.args for call in engine.run.call_args_list]
        rename_out = next(i for i, cmd in enumerate(commands) if cmd[0] == "rename")
        backup_name = commands[rename_out][2]
        candidate_start = next(
            i for i, cmd in enumerate(commands) if cmd == ("start", "paude-proxy-sess")
        )
        state_check = next(i for i, cmd in enumerate(commands) if cmd[0] == "inspect")
        candidate_remove = next(
            i
            for i, cmd in enumerate(commands)
            if cmd == ("rm", "-f", "paude-proxy-sess")
        )
        rename_back = max(i for i, cmd in enumerate(commands) if cmd[0] == "rename")
        assert candidate_start < state_check < candidate_remove < rename_back
        assert ("rm", "-f", backup_name) not in commands
