"""Provider definitions and registry."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for an inference provider.

    Attributes:
        name: Provider identifier (e.g., "vertex", "openai").
        display_name: Human-readable name (e.g., "Vertex AI").
        passthrough_env_vars: Host env vars to forward to container (non-secret).
        secret_env_vars: Host env vars to deliver securely.
        required_secret_env_vars: Secure env vars required for this provider's
            proxy-backed authentication mode. A secret may be optional when the
            provider supports an alternative login flow.
        auth_hint: How to obtain the required secrets, shown when missing.
        passthrough_env_prefixes: Host env var prefixes to forward.
        domain_aliases: Domain aliases to auto-include in allowed-domains.
    """

    name: str
    display_name: str
    passthrough_env_vars: list[str] = field(default_factory=list)
    secret_env_vars: list[str] = field(default_factory=list)
    required_secret_env_vars: list[str] = field(default_factory=list)
    auth_hint: str = ""
    passthrough_env_prefixes: list[str] = field(default_factory=list)
    domain_aliases: list[str] = field(default_factory=list)


_PROVIDERS: dict[str, ProviderConfig] = {
    "vertex": ProviderConfig(
        name="vertex",
        display_name="Vertex AI",
        passthrough_env_vars=[
            "ANTHROPIC_VERTEX_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_PROJECT_ID",
            "GOOGLE_CLOUD_LOCATION",
            "CLOUD_ML_REGION",
        ],
        passthrough_env_prefixes=["CLOUDSDK_AUTH_"],
        domain_aliases=["vertexai"],
    ),
    "openai": ProviderConfig(
        name="openai",
        display_name="OpenAI",
        secret_env_vars=["OPENAI_API_KEY"],
        required_secret_env_vars=["OPENAI_API_KEY"],
        domain_aliases=["openai"],
    ),
    "chatgpt": ProviderConfig(
        name="chatgpt",
        display_name="ChatGPT Plan (OAuth)",
        # No secret env vars: auth is proxy-managed OAuth, not an API key.
        domain_aliases=["chatgpt"],
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        display_name="Anthropic",
        secret_env_vars=["ANTHROPIC_API_KEY"],
        required_secret_env_vars=["ANTHROPIC_API_KEY"],
        domain_aliases=["claude"],
    ),
    "anthropic-oauth": ProviderConfig(
        name="anthropic-oauth",
        display_name="Anthropic (Max OAuth)",
        # The host provides a long-lived `claude setup-token`; paude delivers it
        # to paude-proxy as a secret and the proxy injects it as an
        # `Authorization: Bearer` header. The agent only ever sees the
        # `paude-proxy-managed` sentinel (set per-agent via extra_env_vars).
        secret_env_vars=["CLAUDE_CODE_OAUTH_TOKEN"],
        required_secret_env_vars=["CLAUDE_CODE_OAUTH_TOKEN"],
        auth_hint="run `claude setup-token` on the host and export the token",
        domain_aliases=["claude"],
    ),
    "cursor": ProviderConfig(
        name="cursor",
        display_name="Cursor",
        # Optional: Cursor also supports browser OAuth inside the container.
        secret_env_vars=["CURSOR_API_KEY"],
        domain_aliases=["cursor"],
    ),
    "google": ProviderConfig(
        name="google",
        display_name="Google AI",
        passthrough_env_vars=[
            "GOOGLE_CLOUD_PROJECT",
            "GOOGLE_CLOUD_LOCATION",
            "CLOUD_ML_REGION",
        ],
        passthrough_env_prefixes=["CLOUDSDK_AUTH_"],
        domain_aliases=["vertexai"],
    ),
}


def get_provider(name: str) -> ProviderConfig:
    """Get a provider configuration by name.

    Raises:
        ValueError: If provider name is not registered.
    """
    config = _PROVIDERS.get(name)
    if config is None:
        available = ", ".join(sorted(_PROVIDERS.keys()))
        raise ValueError(f"Unknown provider '{name}'. Available: {available}")
    return config


def list_providers() -> list[str]:
    """List all registered provider names."""
    return sorted(_PROVIDERS.keys())


def check_required_secrets(
    provider_names: Iterable[str],
    environ: Mapping[str, str] | None = None,
) -> None:
    """Fail if any provider's required secret env vars are unset on the host.

    Raises:
        ValueError: Naming each missing variable and the provider needing it.
    """
    env = os.environ if environ is None else environ
    problems: list[str] = []
    for name in dict.fromkeys(provider_names):
        provider = get_provider(name)
        missing = [key for key in provider.required_secret_env_vars if not env.get(key)]
        if not missing:
            continue
        hint = f"; {provider.auth_hint}" if provider.auth_hint else ""
        problems.append(f"{', '.join(missing)} (required by provider '{name}'{hint})")
    if problems:
        raise ValueError(
            "Missing required credentials in the host environment: "
            + "; ".join(problems)
        )
