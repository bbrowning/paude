"""Tests for Docker-specific compatibility behaviors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from paude.agents.codex_config import (
    CODEX_CONFIG_TARGET,
    LEGACY_CODEX_PROFILE_TARGET,
    CodexConfigError,
    reconcile_codex_config,
)
from paude.backends.naming import is_local_backend
from paude.backends.podman import PodmanBackend
from paude.container.engine import ContainerEngine
from paude.container.proxy_runner import ProxyRunner
from paude.container.runner import ContainerRunner


class TestDockerBackendType:
    """Tests for Docker backend type identification."""

    def test_docker_backend_type_is_docker(self) -> None:
        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        assert backend.backend_type == "docker"

    def test_podman_backend_type_is_podman(self) -> None:
        backend = PodmanBackend()
        assert backend.backend_type == "podman"

    def test_docker_is_local_backend(self) -> None:
        assert is_local_backend("docker") is True

    def test_podman_is_local_backend(self) -> None:
        assert is_local_backend("podman") is True


class TestDockerSecretsFallback:
    """Tests for Docker credential handling without secrets."""

    def test_runner_skips_secrets_for_docker(self) -> None:
        """ContainerRunner.create_secret should be a no-op for Docker."""
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        # Should not raise - just silently skips
        runner.create_secret("test-secret", MagicMock())

    def test_runner_skips_remove_secret_for_docker(self) -> None:
        """ContainerRunner.remove_secret should be a no-op for Docker."""
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.remove_secret("test-secret")

    def test_runner_skips_create_secret_from_value_for_docker(self) -> None:
        """ContainerRunner.create_secret_from_value should be a no-op for Docker."""
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.create_secret_from_value("test-secret", "secret-value")

    def test_runner_list_secrets_by_prefix_empty_for_docker(self) -> None:
        """ContainerRunner.list_secrets_by_prefix should return empty for Docker."""
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        assert runner.list_secrets_by_prefix("paude-") == []


class TestDockerMultiNetwork:
    """Tests for Docker multi-network handling in ProxyRunner."""

    def test_podman_multi_network_in_create(self) -> None:
        """Podman uses --network net1,net2 in create command."""
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)
        result = proxy._build_multi_network("internal-net")
        assert result == ["--network", "internal-net,podman"]

    def test_docker_single_network_in_create(self) -> None:
        """Docker uses single --network in create, bridge connected later."""
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)
        result = proxy._build_multi_network("internal-net")
        assert result == ["--network", "internal-net"]

    @patch("subprocess.run")
    def test_docker_connects_bridge_after_create(self, mock_run: MagicMock) -> None:
        """Docker connects bridge network after container creation."""
        mock_run.return_value = MagicMock(returncode=0)
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)
        proxy._connect_bridge_if_needed("my-proxy")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["docker", "network", "connect", "bridge", "my-proxy"]

    def test_podman_skips_bridge_connect(self) -> None:
        """Podman doesn't need separate bridge connect."""
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        proxy = ProxyRunner(runner)
        # Should be a no-op, no subprocess call
        proxy._connect_bridge_if_needed("my-proxy")


class TestDockerAgentContainerNetworkIp:
    """Tests for Docker vs Podman static-IP handling on the agent container."""

    @patch("subprocess.run")
    def test_podman_embeds_ip_in_network(self, mock_run: MagicMock) -> None:
        """Podman embeds the static IP directly in --network."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid\n")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="paude-test",
            image="paude-workspace:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            network="net",
            network_ip="10.0.0.5",
        )
        cmd = mock_run.call_args[0][0]
        assert "--ip" not in cmd
        net_idx = cmd.index("--network")
        assert cmd[net_idx + 1] == "net:ip=10.0.0.5"

    @patch("subprocess.run")
    def test_docker_passes_ip_flag_separately(self, mock_run: MagicMock) -> None:
        """Docker cannot embed the IP in --network; it needs a separate --ip."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid\n")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.create_container(
            name="paude-test",
            image="paude-workspace:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            network="net",
            network_ip="10.0.0.5",
        )
        cmd = mock_run.call_args[0][0]
        net_idx = cmd.index("--network")
        assert cmd[net_idx + 1] == "net"
        ip_idx = cmd.index("--ip")
        assert cmd[ip_idx + 1] == "10.0.0.5"

    @pytest.mark.parametrize("binary", ["podman", "docker"])
    @patch("subprocess.run")
    def test_network_without_ip_unaffected(
        self, mock_run: MagicMock, binary: str
    ) -> None:
        """With no static IP, --network is unchanged for either engine."""
        mock_run.return_value = MagicMock(returncode=0, stdout="cid\n")
        engine = ContainerEngine(binary)
        runner = ContainerRunner(engine)
        runner.create_container(
            name="paude-test",
            image="paude-workspace:latest",
            mounts=[],
            env={},
            workdir="/pvc",
            network="net",
        )
        cmd = mock_run.call_args[0][0]
        assert "--ip" not in cmd
        net_idx = cmd.index("--network")
        assert cmd[net_idx + 1] == "net"


class TestDockerContainerImage:
    """Tests for Docker vs Podman container image inspection."""

    @patch("subprocess.run")
    def test_podman_uses_image_name_format(self, mock_run: MagicMock) -> None:
        """Podman uses {{.ImageName}} format for container image."""
        mock_run.return_value = MagicMock(returncode=0, stdout="myimage:latest\n")
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        result = runner.get_container_image("my-container")
        assert result == "myimage:latest"
        call_args = mock_run.call_args[0][0]
        assert "{{.ImageName}}" in call_args

    @patch("subprocess.run")
    def test_docker_uses_config_image_format(self, mock_run: MagicMock) -> None:
        """Docker uses {{.Config.Image}} format for container image."""
        mock_run.return_value = MagicMock(returncode=0, stdout="myimage:latest\n")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.get_container_image("my-container")
        assert result == "myimage:latest"
        call_args = mock_run.call_args[0][0]
        assert "{{.Config.Image}}" in call_args


class TestDockerListContainers:
    """Tests for Docker list_containers output parsing."""

    @patch("subprocess.run")
    def test_docker_ndjson_single_container(self, mock_run: MagicMock) -> None:
        """Docker outputs one JSON object per line (NDJSON), not an array."""
        import json

        container = {"ID": "abc123", "Names": "test", "Labels": "app=paude,key=val"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(container))
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        assert len(result) == 1
        assert result[0]["ID"] == "abc123"

    @patch("subprocess.run")
    def test_docker_ndjson_multiple_containers(self, mock_run: MagicMock) -> None:
        """Docker outputs multiple JSON objects, one per line."""
        import json

        c1 = {"ID": "abc", "Names": "test1", "Labels": "app=paude"}
        c2 = {"ID": "def", "Names": "test2", "Labels": "app=paude"}
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(c1) + "\n" + json.dumps(c2)
        )
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        assert len(result) == 2
        assert result[0]["ID"] == "abc"
        assert result[1]["ID"] == "def"

    @patch("subprocess.run")
    def test_docker_labels_string_to_dict(self, mock_run: MagicMock) -> None:
        """Docker returns Labels as 'k=v,k2=v2' string; must be normalized to dict."""
        import json

        container = {
            "ID": "abc",
            "Names": "test",
            "Labels": "app=paude,paude.session=my-session,paude.created=2026-01-01",
        }
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(container))
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        labels = result[0]["Labels"]
        assert isinstance(labels, dict)
        assert labels["app"] == "paude"
        assert labels["paude.session"] == "my-session"
        assert labels["paude.created"] == "2026-01-01"

    @patch("subprocess.run")
    def test_podman_json_array_unchanged(self, mock_run: MagicMock) -> None:
        """Podman returns a JSON array with Labels as dict; should work unchanged."""
        import json

        containers = [
            {
                "ID": "abc",
                "Names": "test",
                "Labels": {"app": "paude", "paude.session": "my-session"},
            }
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(containers))
        engine = ContainerEngine("podman")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        assert len(result) == 1
        labels = result[0]["Labels"]
        assert isinstance(labels, dict)
        assert labels["app"] == "paude"

    @patch("subprocess.run")
    def test_docker_label_value_with_equals(self, mock_run: MagicMock) -> None:
        """Label values containing '=' should be preserved correctly."""
        import json

        container = {"ID": "abc", "Labels": "key=val=ue"}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(container))
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        assert result[0]["Labels"]["key"] == "val=ue"

    @patch("subprocess.run")
    def test_docker_empty_labels(self, mock_run: MagicMock) -> None:
        """Empty labels string should produce empty dict."""
        import json

        container = {"ID": "abc", "Labels": ""}
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(container))
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        result = runner.list_containers()
        assert result[0]["Labels"] == {}


class TestDockerVolumePermissions:
    """Tests for /pvc volume ownership reconciliation on start."""

    def _reconcile_exec(self, mock_run: MagicMock) -> list[str]:
        """Return the single guarded root-exec argv issued by the reconcile."""
        exec_calls = [
            c[0][0]
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "--user" in c[0][0]
        ]
        assert len(exec_calls) == 1, exec_calls
        return exec_calls[0]

    @patch("subprocess.run")
    def test_docker_reconciles_volume_ownership_on_start(
        self, mock_run: MagicMock
    ) -> None:
        """Docker should chown /pvc to the pinned runtime user via a root exec."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        backend._setup.fix_volume_permissions("paude-test")
        argv = self._reconcile_exec(mock_run)
        assert argv[:5] == ["docker", "exec", "--user", "root", "paude-test"]
        assert argv[5:7] == ["sh", "-c"]
        script = argv[7]
        # Ownership target is resolved at runtime (not hardcoded), and the
        # recursive chown is guarded by a stat probe so the already-correct case
        # skips the walk.
        assert "id -u paude" in script
        assert 'chown -R "$owner" /pvc' in script
        assert "stat -c %u:%g /pvc" in script

    @patch("subprocess.run")
    def test_podman_reconciles_volume_ownership_on_start(
        self, mock_run: MagicMock
    ) -> None:
        """Podman no longer skips: a reused, UID-drifted volume must be fixed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        backend._setup.fix_volume_permissions("paude-test")
        argv = self._reconcile_exec(mock_run)
        assert argv[:5] == ["podman", "exec", "--user", "root", "paude-test"]
        assert 'chown -R "$owner" /pvc' in argv[-1]
        assert "id -u paude" in argv[-1]


class TestDockerCredentialInjection:
    """Tests for stub ADC credential injection via exec."""

    @patch("subprocess.run")
    def test_inject_stub_credentials_execs_into_container(
        self, mock_run: MagicMock
    ) -> None:
        """_inject_stub_credentials should inject stub ADC via exec."""
        from paude.backends.proxy_config import STUB_ADC_JSON
        from paude.constants import GCP_ADC_TARGET

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        backend._setup.inject_stub_credentials("paude-test")

        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        assert exec_calls[0][1].get("input") == STUB_ADC_JSON
        cmd = exec_calls[0][0][0]
        assert cmd[0] == "docker"
        assert GCP_ADC_TARGET in " ".join(cmd)

    @patch("subprocess.run")
    def test_inject_file_sets_permissions(self, mock_run: MagicMock) -> None:
        """inject_file should chmod 600 the target file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.inject_file("ctr", "secret", "/home/paude/.config/gcloud/adc.json")

        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd
        shell_cmd = cmd[-1]  # the sh -c argument
        assert "chmod 600" in shell_cmd
        assert "mkdir -p" in shell_cmd

    @patch("subprocess.run")
    def test_inject_file_chowns_when_owner_specified(self, mock_run: MagicMock) -> None:
        """inject_file should chown the parent directory and the file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.inject_file(
            "ctr", "secret", "/home/paude/.config/gcloud/adc.json", owner="paude:0"
        )

        cmd = mock_run.call_args[0][0]
        shell_cmd = cmd[-1]
        assert "chown paude:0 /home/paude/.config/gcloud" in shell_cmd
        assert "chmod 600" in shell_cmd

    @patch("subprocess.run")
    def test_inject_file_no_chown_without_owner(self, mock_run: MagicMock) -> None:
        """inject_file should not chown when no owner is specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.inject_file("ctr", "secret", "/home/paude/.config/gcloud/adc.json")

        cmd = mock_run.call_args[0][0]
        shell_cmd = cmd[-1]
        assert "chown" not in shell_cmd

    @patch("subprocess.run")
    def test_inject_stub_credentials_passes_owner(self, mock_run: MagicMock) -> None:
        """_inject_stub_credentials should pass owner='paude:0' to inject_file."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        backend._setup.inject_stub_credentials("paude-test")

        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        shell_cmd = exec_calls[0][0][0][-1]
        assert "chown paude" in shell_cmd


class TestStubCredentialInjection:
    """Tests for stub ADC injection (proxy always active)."""

    @patch("subprocess.run")
    def test_inject_stub_credentials_podman(self, mock_run: MagicMock) -> None:
        """Podman injects stub ADC via exec."""
        from paude.backends.proxy_config import STUB_ADC_JSON

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        backend._setup.inject_stub_credentials("paude-test")

        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        assert exec_calls[0][1].get("input") == STUB_ADC_JSON


class TestCodexConfigReconciliation:
    """Tests for persistent default-config handling shared by Docker and Podman."""

    def test_chatgpt_updates_default_and_removes_legacy_profile(self) -> None:
        backend = PodmanBackend(engine=MagicMock())
        backend._setup._files = MagicMock()
        backend._setup._files.read_file.side_effect = [
            'model = "user-model"\n',
            '[projects."/pvc/workspace"]\ntrust_level = "trusted"\n',
        ]

        backend._setup.configure_codex("paude-test", chatgpt_mode=True)

        write = backend._setup._files.replace_file.call_args
        assert write.args[:2] == ("paude-test", CODEX_CONFIG_TARGET)
        assert 'model = "user-model"' in write.args[2]
        assert 'trust_level = "trusted"' in write.args[2]
        assert 'model_provider = "paude-chatgpt-http"' in write.args[2]
        backend._setup._files.remove_file.assert_called_once_with(
            "paude-test", LEGACY_CODEX_PROFILE_TARGET
        )

    def test_correct_chatgpt_config_is_not_rewritten(self) -> None:
        backend = PodmanBackend(engine=MagicMock())
        backend._setup._files = MagicMock()
        first = reconcile_codex_config(None, None, chatgpt_mode=True)
        backend._setup._files.read_file.side_effect = [first.content, None]

        backend._setup.configure_codex("paude-test", chatgpt_mode=True)

        backend._setup._files.replace_file.assert_not_called()

    def test_invalid_config_is_not_rewritten_or_cleaned_up(self) -> None:
        backend = PodmanBackend(engine=MagicMock())
        backend._setup._files = MagicMock()
        backend._setup._files.read_file.side_effect = [
            "invalid = [toml",
            'model = "legacy"\n',
        ]

        with pytest.raises(CodexConfigError, match="invalid TOML"):
            backend._setup.configure_codex("paude-test", chatgpt_mode=True)

        backend._setup._files.replace_file.assert_not_called()
        backend._setup._files.remove_file.assert_not_called()

    def test_non_chatgpt_removes_container_auth(self) -> None:
        backend = PodmanBackend(engine=MagicMock())
        backend._setup._files = MagicMock()
        backend._setup._files.read_file.side_effect = [None, None]
        backend._setup._runner = MagicMock()

        backend._setup.configure_codex("paude-test", chatgpt_mode=False)

        backend._setup._runner.exec_in_container.assert_called_once_with(
            "paude-test",
            ["rm", "-f", "/pvc/.codex/auth.json"],
            check=False,
        )
