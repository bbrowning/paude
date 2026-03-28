"""Provider definitions and registry."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for an inference provider.

    Attributes:
        name: Provider identifier (e.g., "vertex", "openai").
        display_name: Human-readable name (e.g., "Vertex AI").
        passthrough_env_vars: Host env vars to forward to container (non-secret).
        secret_env_vars: Host env vars to deliver securely.
        passthrough_env_prefixes: Host env var prefixes to forward.
        domain_aliases: Domain aliases to auto-include in allowed-domains.
    """

    name: str
    display_name: str
    passthrough_env_vars: list[str] = field(default_factory=list)
    secret_env_vars: list[str] = field(default_factory=list)
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
        domain_aliases=["openai"],
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        display_name="Anthropic",
        secret_env_vars=["ANTHROPIC_API_KEY"],
        domain_aliases=["claude"],
    ),
    "cursor": ProviderConfig(
        name="cursor",
        display_name="Cursor",
        secret_env_vars=["CURSOR_API_KEY"],
        domain_aliases=["cursor"],
    ),
    "google": ProviderConfig(
        name="google",
        display_name="Google AI",
        passthrough_env_vars=["GOOGLE_CLOUD_PROJECT"],
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
