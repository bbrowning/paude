"""Tests for Docker-specific compatibility behaviors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from paude.backends.podman import PodmanBackend
from paude.backends.shared import is_local_backend
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

    def test_openshift_is_not_local_backend(self) -> None:
        assert is_local_backend("openshift") is False


class TestDockerSecretsFallback:
    """Tests for Docker credential handling without secrets."""

    def test_docker_ensure_gcp_credentials_returns_none(self) -> None:
        """Docker should skip secrets (ADC injected via exec after start)."""
        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        result = backend._ensure_gcp_credentials()
        assert result is None

    @patch("paude.backends.podman.backend.Path")
    def test_podman_ensure_gcp_credentials_creates_secret(
        self, mock_path_class: MagicMock
    ) -> None:
        """Podman should create a secret for GCP ADC."""
        mock_adc_path = MagicMock()
        mock_adc_path.is_file.return_value = True
        mock_path_class.home.return_value.__truediv__ = MagicMock(
            return_value=MagicMock(__truediv__=MagicMock(return_value=mock_adc_path))
        )

        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        with patch.object(backend._runner, "create_secret"):
            result = backend._ensure_gcp_credentials()
            assert result is not None
            assert len(result) == 1
            assert "gcp-adc" in result[0]

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
    """Tests for Docker volume permission fixing."""

    @patch("subprocess.run")
    def test_docker_fixes_volume_permissions_on_start(
        self, mock_run: MagicMock
    ) -> None:
        """Docker should chown /pvc after starting a container."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        backend._fix_volume_permissions("paude-test")
        # Find the chown call
        chown_calls = [c for c in mock_run.call_args_list if "chown" in c[0][0]]
        assert len(chown_calls) == 1
        cmd = chown_calls[0][0][0]
        assert cmd == [
            "docker",
            "exec",
            "--user",
            "root",
            "paude-test",
            "chown",
            "paude:0",
            "/pvc",
        ]

    def test_podman_skips_volume_permission_fix(self) -> None:
        """Podman should skip volume permission fix (uses user namespaces)."""
        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        # Should be a no-op, no subprocess call needed
        backend._fix_volume_permissions("paude-test")


class TestDockerCredentialInjection:
    """Tests for Docker ADC credential injection via exec."""

    @patch("subprocess.run")
    def test_inject_credentials_execs_into_container(self, mock_run: MagicMock) -> None:
        """Docker should inject ADC via exec, not bind mount."""
        from pathlib import Path

        from paude.constants import GCP_ADC_TARGET

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        adc_content = '{"type": "authorized_user"}'

        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)

        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", return_value=adc_content),
        ):
            backend._inject_credentials("paude-test")

        # Should have called docker exec with cat > target
        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        cmd = exec_calls[0][0][0]
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "-i" in cmd
        assert GCP_ADC_TARGET in " ".join(cmd)
        # Credential content piped via stdin
        assert exec_calls[0][1].get("input") == adc_content

    def test_inject_credentials_noop_for_podman(self) -> None:
        """Podman should skip injection (uses secrets instead)."""
        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        # Should not raise or do anything
        backend._inject_credentials("paude-test")

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
        """inject_file should chown the file when owner is specified."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        engine = ContainerEngine("docker")
        runner = ContainerRunner(engine)
        runner.inject_file(
            "ctr", "secret", "/home/paude/.config/gcloud/adc.json", owner="paude:0"
        )

        cmd = mock_run.call_args[0][0]
        shell_cmd = cmd[-1]
        assert "chown paude:0" in shell_cmd
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
    def test_inject_credentials_passes_owner(self, mock_run: MagicMock) -> None:
        """_inject_credentials should pass owner='paude:0' to inject_file."""
        from pathlib import Path

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        adc_content = '{"type": "authorized_user"}'

        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)

        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", return_value=adc_content),
        ):
            backend._inject_credentials("paude-test")

        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        shell_cmd = exec_calls[0][0][0][-1]
        assert "chown paude:0" in shell_cmd


class TestProxyActiveCredentialInjection:
    """Tests for stub ADC injection when proxy is active."""

    @patch("subprocess.run")
    def test_inject_credentials_stub_adc_when_proxy_active(
        self, mock_run: MagicMock
    ) -> None:
        """_inject_credentials injects stub ADC when proxy_active=True."""
        from paude.backends.shared import STUB_ADC_JSON
        from paude.constants import GCP_ADC_TARGET

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        engine = ContainerEngine("docker")
        backend = PodmanBackend(engine=engine)
        backend._inject_credentials("paude-test", proxy_active=True)

        # Should inject stub ADC content
        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        assert exec_calls[0][1].get("input") == STUB_ADC_JSON
        shell_cmd = exec_calls[0][0][0][-1]
        assert GCP_ADC_TARGET in shell_cmd

    @patch("subprocess.run")
    def test_inject_credentials_stub_adc_podman_when_proxy_active(
        self, mock_run: MagicMock
    ) -> None:
        """Podman also injects stub ADC when proxy_active (not no-op)."""
        from paude.backends.shared import STUB_ADC_JSON

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        engine = ContainerEngine("podman")
        backend = PodmanBackend(engine=engine)
        backend._inject_credentials("paude-test", proxy_active=True)

        # Should still inject stub (not skip like normal Podman path)
        exec_calls = [
            c
            for c in mock_run.call_args_list
            if "exec" in c[0][0] and "cat" in " ".join(c[0][0])
        ]
        assert len(exec_calls) == 1
        assert exec_calls[0][1].get("input") == STUB_ADC_JSON
