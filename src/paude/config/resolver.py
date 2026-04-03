"""Layered configuration resolution for paude create."""

from __future__ import annotations

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
    """Fully-resolved create options with provenance tracking."""

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
    pvc_size: SettingValue[str] = field(
        default_factory=lambda: SettingValue("10Gi", "built-in")
    )
    platform: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    gpu: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    openshift_context: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    openshift_namespace: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    openshift_resources: SettingValue[dict[str, dict[str, str]] | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    openshift_build_resources: SettingValue[dict[str, dict[str, str]] | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    provider: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    otel_endpoint: SettingValue[str | None] = field(
        default_factory=lambda: SettingValue(None, "built-in")
    )
    allowed_domains: list[str] = field(default_factory=list)
    allowed_domains_provenance: list[tuple[list[str], Source]] = field(
        default_factory=list
    )


def resolve_create_options(
    *,
    cli_backend: str | None,
    cli_agent: str | None,
    cli_provider: str | None = None,
    cli_yolo: bool | None,
    cli_git: bool | None,
    cli_pvc_size: str | None,
    cli_platform: str | None,
    cli_openshift_context: str | None,
    cli_openshift_namespace: str | None,
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
    """
    result = ResolvedCreateOptions()

    # --- Scalar settings: CLI > project > user > built-in ---
    result.backend = _resolve_scalar(
        cli=cli_backend,
        project=None,  # backend is not a project-level setting
        user=user_defaults.backend,
        builtin="podman",
    )

    result.agent = _resolve_scalar(
        cli=cli_agent,
        project=project_config.create_agent if project_config else None,
        user=user_defaults.agent,
        builtin="claude",
    )

    result.provider = _resolve_scalar(
        cli=cli_provider,
        project=project_config.create_provider if project_config else None,
        user=user_defaults.provider,
        builtin=None,
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

    result.pvc_size = _resolve_scalar(
        cli=cli_pvc_size,
        project=None,
        user=user_defaults.pvc_size,
        builtin="10Gi",
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

    result.openshift_context = _resolve_scalar(
        cli=cli_openshift_context,
        project=None,
        user=user_defaults.openshift.context,
        builtin=None,
    )

    result.openshift_namespace = _resolve_scalar(
        cli=cli_openshift_namespace,
        project=None,
        user=user_defaults.openshift.namespace,
        builtin=None,
    )

    result.openshift_resources = _resolve_scalar(
        cli=None,  # not exposed via CLI or project config
        project=None,
        user=user_defaults.openshift.resources,
        builtin=None,
    )

    result.openshift_build_resources = _resolve_scalar(
        cli=None,  # not exposed via CLI or project config
        project=None,
        user=user_defaults.openshift.build_resources,
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
