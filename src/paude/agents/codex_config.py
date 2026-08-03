"""Merge Paude's required settings into Codex's persistent config."""

from __future__ import annotations

from collections.abc import MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import tomlkit
from tomlkit.exceptions import ParseError

CODEX_CONFIG_TARGET = "/pvc/.codex/config.toml"
LEGACY_CODEX_PROFILE_TARGET = "/pvc/.codex/paude-chatgpt-http.config.toml"
CODEX_CHATGPT_PROVIDER_NAME = "paude-chatgpt-http"

_MANAGED_PROVIDER_VALUES: dict[str, object] = {
    "name": "Paude ChatGPT HTTP",
    "base_url": "https://chatgpt.com/backend-api/codex",
    "wire_api": "responses",
    "requires_openai_auth": True,
    "supports_websockets": False,
}


class CodexConfigError(ValueError):
    """Raised when an existing Codex config cannot be safely updated."""


@dataclass(frozen=True)
class CodexConfigUpdate:
    """Result of reconciling Codex configuration."""

    content: str
    changed: bool
    remove_legacy_profile: bool


def reconcile_codex_config(
    default_content: str | None,
    legacy_content: str | None,
    *,
    chatgpt_mode: bool,
) -> CodexConfigUpdate:
    """Return a minimally updated default config and legacy cleanup state."""
    document = _parse(default_content, CODEX_CONFIG_TARGET)

    if legacy_content is not None:
        legacy = _parse(legacy_content, LEGACY_CODEX_PROFILE_TARGET)
        _remove_managed_values(legacy)
        _merge_active_config(document, legacy)

    if chatgpt_mode:
        _apply_chatgpt_values(document)
    else:
        _remove_managed_values(document)

    content = tomlkit.dumps(document)
    return CodexConfigUpdate(
        content=content,
        changed=content != (default_content or ""),
        remove_legacy_profile=legacy_content is not None,
    )


def _parse(content: str | None, path: str) -> tomlkit.TOMLDocument:
    try:
        return tomlkit.parse(content or "")
    except ParseError as exc:
        raise CodexConfigError(
            f"Cannot update Codex config because {path} is invalid TOML: {exc}"
        ) from exc


def _merge_active_config(
    target: MutableMapping[str, Any], active: MutableMapping[str, Any]
) -> None:
    """Recursively merge the formerly active profile over the default config."""
    for key, value in active.items():
        current = target.get(key)
        if isinstance(current, MutableMapping) and isinstance(value, MutableMapping):
            _merge_active_config(current, value)
        else:
            target[key] = deepcopy(value)


def _apply_chatgpt_values(document: tomlkit.TOMLDocument) -> None:
    document["model_provider"] = CODEX_CHATGPT_PROVIDER_NAME

    providers = _table(document, "model_providers")
    provider = _table(providers, CODEX_CHATGPT_PROVIDER_NAME)
    for key, value in _MANAGED_PROVIDER_VALUES.items():
        provider[key] = value

    features = _table(document, "features")
    features["apps"] = False


def _remove_managed_values(document: MutableMapping[str, Any]) -> None:
    if document.get("model_provider") == CODEX_CHATGPT_PROVIDER_NAME:
        del document["model_provider"]

    providers = document.get("model_providers")
    if isinstance(providers, MutableMapping):
        provider = providers.get(CODEX_CHATGPT_PROVIDER_NAME)
        if isinstance(provider, MutableMapping):
            for key, managed_value in _MANAGED_PROVIDER_VALUES.items():
                if provider.get(key) == managed_value:
                    del provider[key]
            if not provider:
                del providers[CODEX_CHATGPT_PROVIDER_NAME]
        if not providers:
            del document["model_providers"]

    features = document.get("features")
    if isinstance(features, MutableMapping) and features.get("apps") is False:
        del features["apps"]
        if not features:
            del document["features"]


def _table(parent: MutableMapping[str, Any], key: str) -> MutableMapping[str, Any]:
    value: Any = parent.get(key)
    if isinstance(value, MutableMapping):
        return value
    table = tomlkit.table()
    parent[key] = table
    return table
