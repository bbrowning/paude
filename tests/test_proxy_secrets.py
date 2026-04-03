"""Tests for podman secret management for proxy credentials."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.backends.podman.helpers import proxy_secret_name, proxy_secret_prefix
from paude.container.engine import ContainerEngine
from paude.container.proxy_runner import ProxyRunner
from paude.container.runner import ContainerRunner


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
