"""Tests for user config loading and layered config resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paude.config.models import PaudeConfig
from paude.config.resolver import resolve_create_options
from paude.config.user_config import UserDefaults, load_user_defaults


class TestLoadUserDefaults:
    """Tests for load_user_defaults."""

    def test_returns_empty_when_file_missing(self, tmp_path: Path):
        """Returns empty defaults when file does not exist."""
        result = load_user_defaults(tmp_path / "nonexistent.json")
        assert result.backend is None
        assert result.agent is None
        assert result.yolo is None
        assert result.git is None
        assert result.allowed_domains == []

    def test_loads_all_fields(self, tmp_path: Path):
        """Loads all fields from a complete config file."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {
                    "defaults": {
                        "backend": "openshift",
                        "agent": "gemini",
                        "yolo": True,
                        "git": True,
                        "pvc-size": "20Gi",
                        "platform": "linux/amd64",
                        "allowed-domains": ["default", "golang"],
                        "openshift": {
                            "context": "my-cluster",
                            "namespace": "my-ns",
                        },
                    }
                }
            )
        )

        result = load_user_defaults(config)
        assert result.backend == "openshift"
        assert result.agent == "gemini"
        assert result.yolo is True
        assert result.git is True
        assert result.pvc_size == "20Gi"
        assert result.platform == "linux/amd64"
        assert result.allowed_domains == ["default", "golang"]
        assert result.openshift.context == "my-cluster"
        assert result.openshift.namespace == "my-ns"

    def test_loads_partial_fields(self, tmp_path: Path):
        """Loads partial config, leaving unset fields as None."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "openshift"}}))

        result = load_user_defaults(config)
        assert result.backend == "openshift"
        assert result.agent is None
        assert result.yolo is None
        assert result.allowed_domains == []

    def test_warns_on_unknown_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Warns about unknown keys in defaults."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps({"defaults": {"unknown-key": "value", "also-bad": 42}})
        )

        load_user_defaults(config)
        captured = capsys.readouterr()
        assert "Unknown key 'also-bad'" in captured.err
        assert "Unknown key 'unknown-key'" in captured.err

    def test_handles_invalid_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Returns empty defaults on invalid JSON."""
        config = tmp_path / "defaults.json"
        config.write_text("{ invalid json }")

        result = load_user_defaults(config)
        assert result.backend is None
        captured = capsys.readouterr()
        assert "Cannot read" in captured.err

    def test_handles_invalid_defaults_type(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Returns empty defaults when 'defaults' is not an object."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": "not-a-dict"}))

        result = load_user_defaults(config)
        assert result.backend is None
        captured = capsys.readouterr()
        assert "not an object" in captured.err

    def test_loads_openshift_resources(self, tmp_path: Path):
        """Loads resources and build-resources from openshift section."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {
                    "defaults": {
                        "openshift": {
                            "resources": {
                                "requests": {"cpu": "500m", "memory": "2Gi"},
                                "limits": {"cpu": "2", "memory": "4Gi"},
                            },
                            "build-resources": {
                                "requests": {"cpu": "500m", "memory": "1Gi"},
                            },
                        }
                    }
                }
            )
        )

        result = load_user_defaults(config)
        assert result.openshift.resources == {
            "requests": {"cpu": "500m", "memory": "2Gi"},
            "limits": {"cpu": "2", "memory": "4Gi"},
        }
        assert result.openshift.build_resources == {
            "requests": {"cpu": "500m", "memory": "1Gi"},
        }

    def test_openshift_resources_absent_yields_none(self, tmp_path: Path):
        """resources and build_resources are None when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"openshift": {"context": "x"}}}))

        result = load_user_defaults(config)
        assert result.openshift.resources is None
        assert result.openshift.build_resources is None

    def test_openshift_resources_non_dict_warns(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        """Non-dict value for resources warns and yields None."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps(
                {"defaults": {"openshift": {"resources": "bad", "build-resources": 42}}}
            )
        )

        result = load_user_defaults(config)
        assert result.openshift.resources is None
        assert result.openshift.build_resources is None
        captured = capsys.readouterr()
        assert "openshift.resources" in captured.err
        assert "openshift.build-resources" in captured.err


class TestResolveCreateOptions:
    """Tests for resolve_create_options."""

    def _resolve(self, **kwargs):
        """Helper to call resolve_create_options with defaults."""
        defaults = {
            "cli_backend": None,
            "cli_agent": None,
            "cli_yolo": None,
            "cli_git": None,
            "cli_pvc_size": None,
            "cli_platform": None,
            "cli_openshift_context": None,
            "cli_openshift_namespace": None,
            "cli_gpu": None,
            "cli_allowed_domains": None,
            "project_config": None,
            "user_defaults": UserDefaults(),
        }
        defaults.update(kwargs)
        return resolve_create_options(**defaults)

    def test_builtin_defaults(self):
        """Returns built-in defaults when nothing is configured."""
        result = self._resolve()
        assert result.backend.value == "podman"
        assert result.backend.source == "built-in"
        assert result.agent.value == "claude"
        assert result.yolo.value is False
        assert result.git.value is False
        assert result.pvc_size.value == "10Gi"

    def test_cli_overrides_all(self):
        """CLI flags take highest precedence."""
        user = UserDefaults(backend="openshift", agent="gemini", yolo=True)
        project = PaudeConfig(create_agent="cursor")

        result = self._resolve(
            cli_backend="podman",
            cli_agent="claude",
            cli_yolo=False,
            user_defaults=user,
            project_config=project,
        )
        assert result.backend.value == "podman"
        assert result.backend.source == "cli"
        assert result.agent.value == "claude"
        assert result.agent.source == "cli"
        assert result.yolo.value is False
        assert result.yolo.source == "cli"

    def test_project_overrides_user(self):
        """Project config overrides user defaults for agent."""
        user = UserDefaults(agent="gemini")
        project = PaudeConfig(create_agent="cursor")

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.agent.value == "cursor"
        assert result.agent.source == "paude.json"

    def test_user_defaults_override_builtin(self):
        """User defaults override built-in defaults."""
        user = UserDefaults(backend="openshift", yolo=True, git=True)

        result = self._resolve(user_defaults=user)
        assert result.backend.value == "openshift"
        assert result.backend.source == "user defaults"
        assert result.yolo.value is True
        assert result.yolo.source == "user defaults"
        assert result.git.value is True
        assert result.git.source == "user defaults"

    def test_domain_merge_user_and_project(self):
        """Domains merge (union) from user defaults and project config."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=[".vllm.ai", ".openai.com"])

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.allowed_domains == [
            "default",
            "golang",
            ".vllm.ai",
            ".openai.com",
        ]
        assert len(result.allowed_domains_provenance) == 2
        assert result.allowed_domains_provenance[0][1] == "user defaults"
        assert result.allowed_domains_provenance[1][1] == "paude.json"

    def test_domain_merge_deduplicates(self):
        """Domain merge removes duplicates."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=["golang", ".vllm.ai"])

        result = self._resolve(user_defaults=user, project_config=project)
        assert result.allowed_domains == ["default", "golang", ".vllm.ai"]

    def test_cli_domains_override_merged(self):
        """CLI --allowed-domains replaces all merged domains."""
        user = UserDefaults(allowed_domains=["default", "golang"])
        project = PaudeConfig(create_allowed_domains=[".vllm.ai"])

        result = self._resolve(
            cli_allowed_domains=["rust"],
            user_defaults=user,
            project_config=project,
        )
        assert result.allowed_domains == ["rust"]
        assert len(result.allowed_domains_provenance) == 1
        assert result.allowed_domains_provenance[0][1] == "cli"

    def test_no_domains_configured(self):
        """Returns empty domains when nothing is configured."""
        result = self._resolve()
        assert result.allowed_domains == []
        assert result.allowed_domains_provenance == []

    def test_openshift_defaults(self):
        """OpenShift settings resolve from user defaults."""
        from paude.config.user_config import OpenShiftDefaults

        user = UserDefaults(
            openshift=OpenShiftDefaults(context="my-cluster", namespace="my-ns")
        )

        result = self._resolve(user_defaults=user)
        assert result.openshift_context.value == "my-cluster"
        assert result.openshift_context.source == "user defaults"
        assert result.openshift_namespace.value == "my-ns"

    def test_gpu_defaults_to_none(self):
        """GPU defaults to None when not configured."""
        result = self._resolve()
        assert result.gpu.value is None
        assert result.gpu.source == "built-in"

    def test_gpu_from_user_defaults(self):
        """GPU resolves from user defaults."""
        user = UserDefaults(gpu="all")
        result = self._resolve(user_defaults=user)
        assert result.gpu.value == "all"
        assert result.gpu.source == "user defaults"

    def test_gpu_cli_overrides_user(self):
        """CLI --gpu overrides user defaults."""
        user = UserDefaults(gpu="all")
        result = self._resolve(cli_gpu="device=0", user_defaults=user)
        assert result.gpu.value == "device=0"
        assert result.gpu.source == "cli"

    def test_gpu_no_gpu_overrides_user(self):
        """--no-gpu (empty string) overrides user default."""
        user = UserDefaults(gpu="all")
        result = self._resolve(cli_gpu="", user_defaults=user)
        assert result.gpu.value == ""
        assert result.gpu.source == "cli"

    def test_openshift_resources_default_to_none(self):
        """openshift_resources and openshift_build_resources default to None."""
        result = self._resolve()
        assert result.openshift_resources.value is None
        assert result.openshift_resources.source == "built-in"
        assert result.openshift_build_resources.value is None
        assert result.openshift_build_resources.source == "built-in"

    def test_openshift_resources_from_user_defaults(self):
        """openshift_resources resolves from user defaults."""
        from paude.config.user_config import OpenShiftDefaults

        res = {"requests": {"cpu": "500m", "memory": "2Gi"}}
        build_res = {"requests": {"cpu": "250m", "memory": "1Gi"}}
        user = UserDefaults(
            openshift=OpenShiftDefaults(resources=res, build_resources=build_res)
        )

        result = self._resolve(user_defaults=user)
        assert result.openshift_resources.value == res
        assert result.openshift_resources.source == "user defaults"
        assert result.openshift_build_resources.value == build_res
        assert result.openshift_build_resources.source == "user defaults"


class TestUserDefaultsGpu:
    """Tests for GPU field in user defaults."""

    def test_gpu_field_loads_from_json(self, tmp_path: Path):
        """GPU field loads from JSON config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"gpu": "all"}}))

        result = load_user_defaults(config)
        assert result.gpu == "all"

    def test_gpu_device_spec_loads(self, tmp_path: Path):
        """GPU device spec loads correctly."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"gpu": "device=0,1"}}))

        result = load_user_defaults(config)
        assert result.gpu == "device=0,1"

    def test_gpu_defaults_to_none(self, tmp_path: Path):
        """GPU defaults to None when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "podman"}}))

        result = load_user_defaults(config)
        assert result.gpu is None


class TestUserDefaultsOtelEndpoint:
    """Tests for otel-endpoint in user defaults."""

    def test_otel_endpoint_loads_from_json(self, tmp_path: Path):
        """otel-endpoint field loads from JSON config."""
        config = tmp_path / "defaults.json"
        config.write_text(
            json.dumps({"defaults": {"otel-endpoint": "http://collector:4318"}})
        )

        result = load_user_defaults(config)
        assert result.otel_endpoint == "http://collector:4318"

    def test_otel_endpoint_defaults_to_none(self, tmp_path: Path):
        """otel-endpoint defaults to None when not in config."""
        config = tmp_path / "defaults.json"
        config.write_text(json.dumps({"defaults": {"backend": "podman"}}))

        result = load_user_defaults(config)
        assert result.otel_endpoint is None

    def test_otel_endpoint_resolves_from_user_defaults(self):
        """otel-endpoint resolves from user defaults."""
        from paude.config.resolver import resolve_create_options

        user = UserDefaults(otel_endpoint="http://collector:4318")
        result = resolve_create_options(
            cli_backend=None,
            cli_agent=None,
            cli_yolo=None,
            cli_git=None,
            cli_pvc_size=None,
            cli_platform=None,
            cli_openshift_context=None,
            cli_openshift_namespace=None,
            cli_gpu=None,
            cli_allowed_domains=None,
            project_config=None,
            user_defaults=user,
        )
        assert result.otel_endpoint.value == "http://collector:4318"
        assert result.otel_endpoint.source == "user defaults"

    def test_otel_endpoint_cli_overrides_user(self):
        """CLI --otel-endpoint overrides user defaults."""
        from paude.config.resolver import resolve_create_options

        user = UserDefaults(otel_endpoint="http://old:4318")
        result = resolve_create_options(
            cli_backend=None,
            cli_agent=None,
            cli_yolo=None,
            cli_git=None,
            cli_pvc_size=None,
            cli_platform=None,
            cli_openshift_context=None,
            cli_openshift_namespace=None,
            cli_gpu=None,
            cli_allowed_domains=None,
            cli_otel_endpoint="http://new:4318",
            project_config=None,
            user_defaults=user,
        )
        assert result.otel_endpoint.value == "http://new:4318"
        assert result.otel_endpoint.source == "cli"

    def test_otel_endpoint_from_project_config(self):
        """otel-endpoint resolves from project config."""
        from paude.config.resolver import resolve_create_options

        project = PaudeConfig(create_otel_endpoint="http://project:4318")
        result = resolve_create_options(
            cli_backend=None,
            cli_agent=None,
            cli_yolo=None,
            cli_git=None,
            cli_pvc_size=None,
            cli_platform=None,
            cli_openshift_context=None,
            cli_openshift_namespace=None,
            cli_gpu=None,
            cli_allowed_domains=None,
            project_config=project,
            user_defaults=UserDefaults(),
        )
        assert result.otel_endpoint.value == "http://project:4318"
        assert result.otel_endpoint.source == "paude.json"
