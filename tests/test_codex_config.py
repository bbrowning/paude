"""Tests for non-destructive Codex default-config reconciliation."""

from __future__ import annotations

import tomllib

import pytest

from paude.agents.codex_config import CodexConfigError, reconcile_codex_config


def _load(content: str) -> dict[str, object]:
    return tomllib.loads(content)


def test_fresh_chatgpt_config_contains_required_settings() -> None:
    update = reconcile_codex_config(None, None, chatgpt_mode=True)

    config = _load(update.content)
    assert config["model_provider"] == "paude-chatgpt-http"
    provider = config["model_providers"]["paude-chatgpt-http"]  # type: ignore[index]
    assert provider == {
        "name": "Paude ChatGPT HTTP",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "supports_websockets": False,
    }
    assert config["features"] == {"apps": False}
    assert update.changed is True


def test_preserves_user_settings_comments_and_project_trust() -> None:
    existing = """# personal config
model = "gpt-custom"
model_reasoning_effort = "high"

[projects."/pvc/workspace"]
trust_level = "trusted" # keep this decision

[mcp_servers.example]
command = "example-mcp"
"""

    update = reconcile_codex_config(existing, None, chatgpt_mode=True)

    assert "# personal config" in update.content
    assert "# keep this decision" in update.content
    config = _load(update.content)
    assert config["model"] == "gpt-custom"
    assert config["model_reasoning_effort"] == "high"
    assert config["projects"] == {"/pvc/workspace": {"trust_level": "trusted"}}
    assert config["mcp_servers"] == {"example": {"command": "example-mcp"}}


def test_correct_config_is_byte_for_byte_noop() -> None:
    first = reconcile_codex_config(None, None, chatgpt_mode=True)
    second = reconcile_codex_config(first.content, None, chatgpt_mode=True)

    assert second.content == first.content
    assert second.changed is False


def test_only_required_values_are_corrected() -> None:
    existing = """model = "user-model"
model_provider = "another-provider"

[model_providers.paude-chatgpt-http]
base_url = "https://wrong.invalid"
custom_option = "preserve-me"

[features]
apps = true
another_feature = true
"""

    update = reconcile_codex_config(existing, None, chatgpt_mode=True)

    config = _load(update.content)
    assert config["model"] == "user-model"
    provider = config["model_providers"]["paude-chatgpt-http"]  # type: ignore[index]
    assert provider["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert provider["custom_option"] == "preserve-me"
    assert config["features"] == {"apps": False, "another_feature": True}


def test_legacy_profile_merges_over_default_then_is_removed() -> None:
    default = """model = "default-model"

[projects."/default"]
trust_level = "trusted"
"""
    legacy = """model = "active-model"
model_provider = "paude-chatgpt-http"

[model_providers.paude-chatgpt-http]
name = "Paude ChatGPT HTTP"
base_url = "https://chatgpt.com/backend-api/codex"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false

[features]
apps = false

[projects."/pvc/workspace"]
trust_level = "trusted"
"""

    update = reconcile_codex_config(default, legacy, chatgpt_mode=True)

    config = _load(update.content)
    assert config["model"] == "active-model"
    assert config["projects"] == {
        "/default": {"trust_level": "trusted"},
        "/pvc/workspace": {"trust_level": "trusted"},
    }
    assert update.remove_legacy_profile is True


def test_non_chatgpt_removes_only_exact_managed_values() -> None:
    existing = """model = "user-model"
model_provider = "paude-chatgpt-http"

[model_providers.paude-chatgpt-http]
name = "Paude ChatGPT HTTP"
base_url = "https://user-modified.example"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
custom_option = "preserve-me"

[features]
apps = false
another_feature = true
"""

    update = reconcile_codex_config(existing, None, chatgpt_mode=False)

    config = _load(update.content)
    assert "model_provider" not in config
    provider = config["model_providers"]["paude-chatgpt-http"]  # type: ignore[index]
    assert provider == {
        "base_url": "https://user-modified.example",
        "custom_option": "preserve-me",
    }
    assert config["features"] == {"another_feature": True}


@pytest.mark.parametrize("legacy", [False, True])
def test_invalid_toml_fails_without_a_replacement(legacy: bool) -> None:
    default_content = "valid = true\n" if legacy else "not = [valid"
    legacy_content = "not = [valid" if legacy else None

    with pytest.raises(CodexConfigError, match="invalid TOML"):
        reconcile_codex_config(
            default_content,
            legacy_content,
            chatgpt_mode=True,
        )
