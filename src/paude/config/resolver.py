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

    The ``agent`` and ``provider`` scalars are retained for backward
    compatibility and always describe the *primary* (first) agent and its
    resolved provider. The ``agents``/``providers`` lists hold the full
    resolved sets, and ``agent_providers`` maps each agent to the provider it
    will use (first = primary).

    Note: session creation currently uses only the primary agent/provider
    scalars; the ``agents``/``providers``/``agent_providers`` lists are consumed
    by the ``--dry-run`` preview only. Multi-agent creation is not yet
    implemented, so a real create ignores all but the primary agent.
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
    # Explicit --providers entries never assigned to any agent (real create warns).
    dropped_providers: list[str] = field(default_factory=list)
    # Derived per-agent provider mapping (ordered, first = primary).
    agent_providers: list[tuple[str, str]] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)
    allowed_domains_provenance: list[tuple[list[str], Source]] = field(
        default_factory=list
    )
    forward_ports: SettingValue[list[str]] = field(
        default_factory=lambda: SettingValue([], "built-in")
    )


def resolve_create_options(
    *,
    cli_backend: str | None,
    cli_agent: str | None,
    cli_provider: str | None = None,
    cli_agents: list[str] | None = None,
    cli_providers: list[str] | None = None,
    cli_yolo: bool | None,
    cli_git: bool | None,
    cli_platform: str | None,
    cli_gpu: str | None,
    cli_allowed_domains: list[str] | None,
    cli_otel_endpoint: str | None = None,
    cli_forward_ports: list[str] | None = None,
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
    if cli_provider is not None and cli_providers is not None:
        raise ValueError("Specify --provider or --providers, not both.")

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

    # --- Forward ports: first non-empty layer wins (no merge) ---
    result.forward_ports = _resolve_list(
        cli=cli_forward_ports,
        project=project_config.create_forward_ports if project_config else None,
        user=user_defaults.forward_ports,
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


def _resolve_list(
    *,
    cli: list[str] | None,
    project: list[str] | None,
    user: list[str] | None,
) -> SettingValue[list[str]]:
    """Resolve a list setting: first non-empty layer wins, no merge.

    Unlike allowed-domains (which merges), forward ports are taken wholesale
    from the highest-precedence layer that provides any, so a project or user
    can fully override the layer below it.
    """
    if cli:
        return SettingValue(list(cli), "cli")
    if project:
        return SettingValue(list(project), "paude.json")
    if user:
        return SettingValue(list(user), "user defaults")
    return SettingValue([], "built-in")


def _resolve_option_list(
    *,
    cli: list[str] | None,
    project: list[str] | None,
    user: list[str] | None,
    builtin: list[str],
) -> tuple[list[str], Source]:
    """Resolve a list-valued setting using precedence order.

    An empty list at any layer is treated as "not set" and falls through to
    the next layer. The returned list is de-duplicated preserving order.
    """
    if cli:
        return _dedupe(cli), "cli"
    if project:
        return _dedupe(project), "paude.json"
    if user:
        return _dedupe(user), "user defaults"
    return _dedupe(builtin), "built-in"


def _resolve_agents_and_providers(
    *,
    result: ResolvedCreateOptions,
    cli_agent: str | None,
    cli_agents: list[str] | None,
    cli_provider: str | None,
    cli_providers: list[str] | None,
    project_config: PaudeConfig | None,
    user_defaults: UserDefaults,
) -> None:
    """Resolve the agent and provider lists, then derive per-agent providers.

    Populates ``agents``/``providers`` (with provenance), the ``agent``/
    ``provider`` primary scalars (for back-compat), and the ``agent_providers``
    mapping. Validates every agent name and agent/provider combination.
    """
    # A singular flag/hint contributes a one-element list at its layer.
    agents, agents_source = _resolve_option_list(
        cli=cli_agents if cli_agents is not None else _as_list(cli_agent),
        project=_project_list(project_config, "create_agents", "create_agent"),
        user=user_defaults.agents or _as_list(user_defaults.agent),
        builtin=["claude"],
    )
    if not agents:
        raise ValueError(
            "Agent name cannot be empty. Specify a non-empty --agent/--agents value."
        )
    result.agents = agents
    result.agents_provenance = [(agents, agents_source)]
    result.agent = SettingValue(agents[0], agents_source)

    provider_pool, providers_source = _resolve_option_list(
        cli=cli_providers if cli_providers is not None else _as_list(cli_provider),
        project=_project_list(project_config, "create_providers", "create_provider"),
        user=user_defaults.providers or _as_list(user_defaults.provider),
        builtin=[],
    )
    if not provider_pool and providers_source != "built-in":
        raise ValueError(
            "Provider name cannot be empty. Specify a non-empty "
            "--provider/--providers value."
        )

    # The primary provider scalar mirrors the highest-precedence explicit
    # provider (first in the pool), or None when nothing was configured.
    primary_provider = provider_pool[0] if provider_pool else None
    result.provider = SettingValue(
        primary_provider,
        providers_source if provider_pool else "built-in",
    )

    # Derive the provider each agent will use. The primary agent honors the
    # explicit primary provider (back-compat with `--provider`); every other
    # agent uses its own default provider.
    result.agent_providers = _derive_agent_providers(agents, primary_provider)

    # Only list providers that are actually assigned to some agent: an
    # explicit --providers entry beyond what the primary agent consumes is
    # never applied to any agent, so including it here would misleadingly
    # suggest it's in effect.
    used = _dedupe(provider for _, provider in result.agent_providers)
    used_explicit = [p for p in provider_pool if p in used]
    auto_added = [p for p in used if p not in provider_pool]
    result.providers = used
    result.providers_provenance = []
    if used_explicit:
        result.providers_provenance.append((used_explicit, providers_source))
    if auto_added:
        result.providers_provenance.append((auto_added, "built-in"))
    result.dropped_providers = [p for p in provider_pool if p not in used]


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


def _derive_agent_providers(
    agents: list[str], primary_provider: str | None
) -> list[tuple[str, str]]:
    """Map each agent to the provider it will use, validating each combination.

    The primary (first) agent uses ``primary_provider`` when one was supplied,
    otherwise its own default. Every subsequent agent uses its default. Each
    (agent, provider) pair is validated via ``resolve_agent_provider``.

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
    for index, agent in enumerate(agents):
        # Validate the agent name (raises with a friendly message if unknown).
        get_agent(agent)

        if index == 0 and primary_provider is not None:
            provider = primary_provider
        else:
            provider = DEFAULT_PROVIDER.get(agent) or ""

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
