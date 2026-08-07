"""Layered configuration resolution for paude create."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from paude.config.models import PaudeConfig
from paude.config.user_config import UserDefaults

Source = Literal["cli", "paude.json", "user defaults", "built-in"]

T = TypeVar("T")


@dataclass
class SettingValue(Generic[T]):
    """A resolved value with its provenance source."""

    value: T
    source: Source


def format_setting(name: str, setting: SettingValue[Any]) -> str:
    """Format a setting with provenance for display."""
    val = setting.value
    display = val if val is not None else "(not set)"
    return f"  {name}: {display}  ({setting.source})"


@dataclass
class ResolvedCreateOptions:
    """Fully-resolved create options with provenance tracking.

    The ``agent`` and ``provider`` scalars describe the primary agent and its
    resolved provider. ``agents`` is the exact install set, ``providers`` is
    the credential-provider set, and ``agent_providers`` is the effective
    mapping for every installed agent.

    Session creation carries the complete ``agent_providers`` list through the
    image, container, and lifecycle paths. The scalar fields remain for
    backwards-compatible display and single-agent callers.
    """

    backend: SettingValue[str] = field(
        default_factory=lambda: SettingValue("podman", "built-in")
    )
    agent: SettingValue[str] = field(
        default_factory=lambda: SettingValue("claude", "built-in")
    )
    yolo: SettingValue[bool] = field(
        default_factory=lambda: SettingValue(False, "built-in")
    )
    git: SettingValue[bool] = field(
        default_factory=lambda: SettingValue(False, "built-in")
    )
    platform: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    gpu: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    provider: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    otel_endpoint: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    agents: list[str] = field(default_factory=lambda: ["claude"])
    agents_provenance: list[tuple[list[str], Source]] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    providers_provenance: list[tuple[list[str], Source]] = field(default_factory=list)
    # Effective per-agent provider mapping (ordered, first = primary).
    agent_providers: list[tuple[str, str]] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    allowed_domains_provenance: list[tuple[list[str], Source]] = field(
        default_factory=list
    )


def resolve_create_options(
    *,
    cli_backend: str | None,
    cli_agent: str | None,
    cli_provider: str | None = None,
    cli_agents: list[str] | None = None,
    cli_providers: list[str] | None = None,
    cli_agent_providers: dict[str, str] | None = None,
    cli_yolo: bool | None,
    cli_git: bool | None,
    cli_platform: str | None,
    cli_gpu: str | None,
    cli_allowed_domains: list[str] | None,
    cli_otel_endpoint: str | None = None,
    project_config: PaudeConfig | None,
    user_defaults: UserDefaults,
) -> ResolvedCreateOptions:
    """Resolve create options using layered precedence.

    Precedence (highest wins):
    1. CLI flags (explicit)
    2. Project config (paude.json "create" section)
    3. User defaults (~/.config/paude/defaults.json)
    4. Built-in defaults

    Domains merge (union) across user defaults and project config,
    unless CLI --allowed-domains was explicitly provided.

    The singular ``cli_agent``/``cli_provider`` arguments and the list-valued
    ``cli_agents``/``cli_providers`` arguments are mutually exclusive: passing
    both a singular flag and its plural counterpart raises ``ValueError``.

    Raises:
        ValueError: On unsupported backend, conflicting singular/plural flags,
            an unknown agent name, or an unsupported agent/provider combination.
    """
    result = ResolvedCreateOptions()

    if cli_agent is not None and cli_agents is not None:
        raise ValueError("Specify --agent or --agents, not both.")
    if cli_provider is not None and cli_agent_providers is not None:
        raise ValueError("Specify --provider or --agent-provider, not both.")

    if user_defaults.backend not in (None, "podman", "docker"):
        raise ValueError(
            f"Unsupported backend '{user_defaults.backend}' in user defaults. "
            "Supported backends are: podman, docker."
        )

    # --- Scalar settings: CLI > project > user > built-in ---
    result.backend = _resolve_scalar(
        cli=cli_backend,
        project=None,  # backend is not a project-level setting
        user=user_defaults.backend,
        builtin="podman",
    )

    # --- Agent/provider lists (CLI > project > user > built-in) ---
    _resolve_agents_and_providers(
        result=result,
        cli_agent=cli_agent,
        cli_agents=cli_agents,
        cli_provider=cli_provider,
        cli_providers=cli_providers,
        cli_agent_providers=cli_agent_providers,
        project_config=project_config,
        user_defaults=user_defaults,
    )

    result.otel_endpoint = _resolve_scalar(
        cli=cli_otel_endpoint,
        project=project_config.create_otel_endpoint if project_config else None,
        user=user_defaults.otel_endpoint,
        builtin=None,
    )

    result.yolo = _resolve_scalar(
        cli=cli_yolo,
        project=None,
        user=user_defaults.yolo,
        builtin=False,
    )

    result.git = _resolve_scalar(
        cli=cli_git,
        project=None,
        user=user_defaults.git,
        builtin=False,
    )

    result.platform = _resolve_scalar(
        cli=cli_platform,
        project=None,
        user=user_defaults.platform,
        builtin=None,
    )

    result.gpu = _resolve_scalar(
        cli=cli_gpu,
        project=None,
        user=user_defaults.gpu,
        builtin=None,
    )

    # --- Domain resolution ---
    _resolve_domains(
        result=result,
        cli_allowed_domains=cli_allowed_domains,
        project_config=project_config,
        user_defaults=user_defaults,
    )

    return result


def _resolve_scalar(
    *,
    cli: T | None,
    project: T | None,
    user: T | None,
    builtin: T,
) -> SettingValue[T]:
    """Resolve a single setting using precedence order."""
    if cli is not None:
        return SettingValue(cli, "cli")
    if project is not None:
        return SettingValue(project, "paude.json")
    if user is not None:
        return SettingValue(user, "user defaults")
    return SettingValue(builtin, "built-in")


def _dedupe(items: Iterable[str]) -> list[str]:
    """Return items with duplicates removed, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _resolve_option_list(
    *,
    cli: list[str] | None,
    project: list[str] | None,
    user: list[str] | None,
    builtin: list[str],
    deduplicate: bool = True,
) -> tuple[list[str], Source]:
    """Resolve a list-valued setting using precedence order.

    An empty list at any layer is treated as "not set" and falls through to
    the next layer. By default, the returned list is de-duplicated preserving
    order. Positional lists can disable de-duplication while still discarding
    empty entries.
    """

    def normalize(values: list[str]) -> list[str]:
        return _dedupe(values) if deduplicate else [value for value in values if value]

    if cli:
        return normalize(cli), "cli"
    if project:
        return normalize(project), "paude.json"
    if user:
        return normalize(user), "user defaults"
    return normalize(builtin), "built-in"


def _resolve_agents_and_providers(
    *,
    result: ResolvedCreateOptions,
    cli_agent: str | None,
    cli_agents: list[str] | None,
    cli_provider: str | None,
    cli_providers: list[str] | None,
    cli_agent_providers: dict[str, str] | None,
    project_config: PaudeConfig | None,
    user_defaults: UserDefaults,
) -> None:
    """Resolve installed agents, credentials, and per-agent mappings.

    Populates ``agents``/``providers`` (with provenance), the ``agent``/
    ``provider`` primary scalars (for back-compat), and the ``agent_providers``
    mapping. Validates every agent name and agent/provider combination.
    """
    agents, agents_source = _resolve_option_list(
        cli=cli_agents if cli_agents is not None else _as_list(cli_agent),
        project=_project_list(project_config, "create_agents", "create_agent"),
        user=user_defaults.agents or _as_list(user_defaults.agent),
        builtin=["claude"],
        deduplicate=False,
    )
    if not agents:
        raise ValueError(
            "Agent name cannot be empty. Specify a non-empty --agent/--agents value."
        )
    duplicates = [name for index, name in enumerate(agents) if name in agents[:index]]
    if duplicates:
        names = ", ".join(_dedupe(duplicates))
        raise ValueError(f"Duplicate agent names are not allowed: {names}.")
    result.agents = agents
    result.agents_provenance = [(agents, agents_source)]
    result.agent = SettingValue(agents[0], agents_source)

    credential_providers, providers_source = _resolve_option_list(
        cli=cli_providers,
        project=project_config.create_providers if project_config else None,
        user=user_defaults.providers,
        builtin=[],
    )
    if not credential_providers and providers_source != "built-in":
        raise ValueError(
            "Provider name cannot be empty. Specify a non-empty "
            "--provider/--providers value."
        )

    requested_mappings, mapping_source = _resolve_agent_provider_mappings(
        primary_agent=agents[0],
        cli_provider=cli_provider,
        cli_agent_providers=cli_agent_providers,
        project_config=project_config,
        user_defaults=user_defaults,
    )
    result.agent_providers = _derive_agent_providers(agents, requested_mappings)
    result.provider = SettingValue(result.agent_providers[0][1], mapping_source)

    from paude.providers import get_provider

    for provider_name in credential_providers:
        get_provider(provider_name)
    mapped_providers = _dedupe(provider for _, provider in result.agent_providers)
    if credential_providers:
        missing = [
            provider
            for provider in mapped_providers
            if provider not in credential_providers
        ]
        if missing:
            raise ValueError(
                "Credential providers must include every mapped provider; missing: "
                + ", ".join(missing)
            )
        result.providers = credential_providers
        result.providers_provenance = [(credential_providers, providers_source)]
    else:
        result.providers = mapped_providers
        result.providers_provenance = [(mapped_providers, mapping_source)]


def _as_list(value: str | None) -> list[str] | None:
    """Wrap a scalar into a one-element list, or None when unset."""
    return [value] if value is not None else None


def _project_list(
    project_config: PaudeConfig | None,
    list_attr: str,
    scalar_attr: str,
) -> list[str] | None:
    """Extract a project-config list, falling back to its singular scalar."""
    if project_config is None:
        return None
    plural: list[str] = getattr(project_config, list_attr)
    if plural:
        return plural
    return _as_list(getattr(project_config, scalar_attr))


def _resolve_agent_provider_mappings(
    *,
    primary_agent: str,
    cli_provider: str | None,
    cli_agent_providers: dict[str, str] | None,
    project_config: PaudeConfig | None,
    user_defaults: UserDefaults,
) -> tuple[dict[str, str], Source]:
    """Resolve the highest-precedence explicit mapping layer."""
    if cli_agent_providers:
        return dict(cli_agent_providers), "cli"
    if cli_provider is not None:
        return {primary_agent: cli_provider}, "cli"
    if project_config is not None:
        if project_config.create_agent_providers and project_config.create_provider:
            raise ValueError(
                "Specify provider or agent-providers in project config, not both."
            )
        if project_config.create_agent_providers:
            return dict(project_config.create_agent_providers), "paude.json"
        if project_config.create_provider is not None:
            return {primary_agent: project_config.create_provider}, "paude.json"
    if user_defaults.agent_providers and user_defaults.provider:
        raise ValueError(
            "Specify provider or agent-providers in user defaults, not both."
        )
    if user_defaults.agent_providers:
        return dict(user_defaults.agent_providers), "user defaults"
    if user_defaults.provider is not None:
        return {primary_agent: user_defaults.provider}, "user defaults"
    return {}, "built-in"


def _derive_agent_providers(
    agents: list[str], mappings: dict[str, str]
) -> list[tuple[str, str]]:
    """Map each agent to the provider it will use, validating each combination.

    Explicit mappings override named agents. Unmapped agents use their default.
    Each assigned pair is validated via
    ``resolve_agent_provider``.

    Raises:
        ValueError: If an agent name is unknown or the agent/provider
            combination is unsupported.
    """
    from paude.agents import get_agent
    from paude.providers.agent_providers import (
        DEFAULT_PROVIDER,
        resolve_agent_provider,
    )

    mapping: list[tuple[str, str]] = []
    unknown = [agent for agent in mappings if agent not in agents]
    if unknown:
        raise ValueError(
            "Provider mappings reference agents that are not installed: "
            + ", ".join(unknown)
        )
    for agent in agents:
        # Validate the agent name (raises with a friendly message if unknown).
        get_agent(agent)

        provider = mappings.get(agent, DEFAULT_PROVIDER.get(agent) or "")
        if not provider:
            raise ValueError("Provider name cannot be empty.")

        # Validate the combination and resolve the effective provider name.
        provider_config, _ = resolve_agent_provider(agent, provider or None)
        mapping.append((agent, provider_config.name))
    return mapping


def _resolve_domains(
    *,
    result: ResolvedCreateOptions,
    cli_allowed_domains: list[str] | None,
    project_config: PaudeConfig | None,
    user_defaults: UserDefaults,
) -> None:
    """Resolve allowed domains with merge/override semantics.

    CLI --allowed-domains overrides entirely.
    Otherwise, user defaults and project config domains are merged (union).
    """
    if cli_allowed_domains is not None:
        result.allowed_domains = cli_allowed_domains
        result.allowed_domains_provenance = [
            (cli_allowed_domains, "cli"),
        ]
        return

    merged: list[str] = []
    seen: set[str] = set()
    provenance: list[tuple[list[str], Source]] = []

    # User defaults domains
    if user_defaults.allowed_domains:
        for d in user_defaults.allowed_domains:
            if d not in seen:
                merged.append(d)
                seen.add(d)
        provenance.append((list(user_defaults.allowed_domains), "user defaults"))

    # Project config domains
    project_domains = project_config.create_allowed_domains if project_config else []
    if project_domains:
        for d in project_domains:
            if d not in seen:
                merged.append(d)
                seen.add(d)
        provenance.append((project_domains, "paude.json"))

    result.allowed_domains = merged
    result.allowed_domains_provenance = provenance
