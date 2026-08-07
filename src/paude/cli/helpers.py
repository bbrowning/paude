"""Shared helper functions for CLI commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from paude.backends import PodmanBackend
from paude.backends.base import Backend, Session
from paude.cli.app import BackendType
from paude.config.models import PaudeConfig
from paude.container.engine import ContainerEngine
from paude.session_discovery import (
    collect_all_sessions,
    find_workspace_session,
)

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition


def find_session_backend(
    session_name: str,
    connect_timeout: int | None = None,
) -> tuple[BackendType, Backend] | None:
    """Find which backend contains the given session.

    Checks the local registry first for SSH sessions, then probes
    local Podman and Docker engines.

    Args:
        session_name: Name of the session to find.
    Returns:
        Tuple of (backend_type, backend_instance) if found, None otherwise.
        The backend instance uses either Podman or Docker.
    """
    # Check registry for SSH sessions first
    from paude.registry import SessionRegistry

    entry = SessionRegistry().get(session_name)
    if entry and entry.ssh_host:
        backend = _build_ssh_backend(entry, connect_timeout=connect_timeout)
        if backend is not None:
            bt = BackendType(entry.engine)
            return (bt, backend)

    # Try Podman first
    try:
        podman = PodmanBackend()
        if podman.get_session(session_name) is not None:
            return (BackendType.podman, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Try Docker
    try:
        docker = PodmanBackend(engine=ContainerEngine("docker"))
        if docker.get_session(session_name) is not None:
            return (BackendType.docker, docker)
    except Exception:  # noqa: S110 - Docker may not be available
        pass

    return None


def _build_ssh_backend(
    entry: object,
    connect_timeout: int | None = None,
) -> PodmanBackend | None:
    """Reconstruct a PodmanBackend with SSH transport from a registry entry."""
    from paude.backends.ssh import build_ssh_backend

    return build_ssh_backend(entry, connect_timeout=connect_timeout)


def _get_backend_instance(
    backend: BackendType,
    ssh_host: str | None = None,
    ssh_key: str | None = None,
) -> Backend:
    """Create a backend instance based on the backend type.

    Args:
        backend: The backend type to create.
        ssh_host: Optional SSH host for remote execution.
        ssh_key: Optional SSH key path.

    Returns:
        Backend instance configured for Podman or Docker.
    """
    transport = None
    if ssh_host:
        from paude.transport.ssh import SshTransport, parse_ssh_host

        host, port = parse_ssh_host(ssh_host)
        transport = SshTransport(host, key=ssh_key, port=port)
    engine = ContainerEngine(backend.value, transport=transport)
    return PodmanBackend(engine=engine)


def _auto_select_session(
    *,
    status_filter: str | None = None,
    no_sessions_hints: list[str],
    multi_hint_format: str = "  paude start {name}  # {backend_type}",
) -> tuple[Session, Backend]:
    """Auto-select a session when no name/backend is specified.

    Searches workspace sessions first, then all sessions. Exits with
    code 1 if no sessions found or multiple sessions found.

    Args:
        status_filter: Optional status filter (e.g. "running").
        no_sessions_hints: Messages to show when no sessions found.
        multi_hint_format: Format string for each session in multi-session
            list. Available placeholders: {name}, {backend_type}, {status},
            {workspace}.

    Returns:
        Tuple of (session, backend) for the selected session.
    """
    workspace_match = find_workspace_session(status_filter=status_filter)
    if workspace_match:
        return workspace_match

    all_sessions, _reachable = collect_all_sessions(status_filter=status_filter)
    if not all_sessions:
        for hint in no_sessions_hints:
            typer.echo(hint, err=True)
        raise typer.Exit(1)
    if len(all_sessions) == 1:
        return all_sessions[0]

    qualifier = "running " if status_filter == "running" else ""
    typer.echo(f"Multiple {qualifier}sessions found. Specify one:", err=True)
    typer.echo("", err=True)
    for s, _ in all_sessions:
        workspace_str = str(s.workspace)
        if len(workspace_str) > 35:
            workspace_str = "..." + workspace_str[-32:]
        typer.echo(
            multi_hint_format.format(
                name=s.name,
                backend_type=s.backend_type,
                status=s.status,
                workspace=workspace_str,
            ),
            err=True,
        )
    raise typer.Exit(1)


def _detect_dev_script_dir() -> Path | None:
    """Detect the dev-mode script directory.

    Returns the project root if a containers/paude/Dockerfile exists
    relative to the package location, otherwise None.
    """
    # Support both src layout (src/paude/cli/helpers.py → 4 levels)
    # and flat layout (paude/cli/helpers.py → 3 levels)
    base = Path(__file__)
    for depth in range(4, 2, -1):
        dev_path = base.parents[depth - 1]
        if (dev_path / "containers" / "paude" / "Dockerfile").exists():
            return dev_path
    return None


def _parse_agent_args(claude_args: str | None) -> list[str]:
    """Parse agent args string into a list using shlex."""
    import shlex

    if not claude_args:
        return []
    try:
        return shlex.split(claude_args)
    except ValueError as e:
        typer.echo(f"Error parsing --args: {e}", err=True)
        raise typer.Exit(1) from None


# Backward-compat alias
_parse_claude_args = _parse_agent_args


def _split_list_option(values: list[str] | None) -> list[str] | None:
    """Normalize a repeatable, comma-separated CLI option into a list.

    Each occurrence may itself contain comma-separated entries, so
    ``--agents a,b --agents c`` yields ``["a", "b", "c"]``. Whitespace is
    stripped and empty entries are dropped. Returns None when the option was
    not provided (``values is None``) or contained no usable entries, so the
    resolver can distinguish "unset" from an explicit list.
    """
    if values is None:
        return None
    items = [
        entry.strip() for value in values for entry in value.split(",") if entry.strip()
    ]
    return items or None


def _parse_agent_provider_options(
    values: list[str] | None,
) -> dict[str, str] | None:
    """Parse repeatable/comma-separated ``AGENT=PROVIDER`` mappings."""
    items = _split_list_option(values)
    if items is None:
        return None
    mappings: dict[str, str] = {}
    for item in items:
        if item.count("=") != 1:
            raise ValueError(
                f"Invalid agent-provider mapping '{item}'; expected AGENT=PROVIDER."
            )
        agent, provider = (part.strip() for part in item.split("=", 1))
        if not agent or not provider:
            raise ValueError(
                f"Invalid agent-provider mapping '{item}'; expected AGENT=PROVIDER."
            )
        if agent in mappings:
            raise ValueError(f"Duplicate provider mapping for agent '{agent}'.")
        mappings[agent] = provider
    return mappings


def _get_provider_aliases(
    provider_name: str | None, agent_name: str
) -> list[str] | None:
    """Resolve provider domain aliases for an agent."""
    from paude.providers import get_provider
    from paude.providers.agent_providers import DEFAULT_PROVIDER

    resolved = provider_name or DEFAULT_PROVIDER.get(agent_name)
    if not resolved:
        return None
    try:
        return get_provider(resolved).domain_aliases
    except ValueError:
        return None


def _expand_allowed_domains(
    allowed_domains: list[str] | None,
    extra_aliases: list[str] | None = None,
    provider_aliases: list[str] | None = None,
    required_aliases: list[str] | None = None,
) -> list[str]:
    """Expand domain aliases, defaulting to ["default"].

    Args:
        allowed_domains: Raw domain list from CLI, or None for defaults.
        extra_aliases: Agent-specific aliases to add on top of BASE_ALIASES
            when expanding "default". If None, falls back to DEFAULT_ALIASES.
        provider_aliases: Provider-specific domain aliases to merge in.
    """
    from paude.domains import expand_domains

    if provider_aliases:
        merged = list(extra_aliases or [])
        for alias in provider_aliases:
            if alias not in merged:
                merged.append(alias)
        extra_aliases = merged

    domains_input = allowed_domains if allowed_domains else ["default"]
    expanded = expand_domains(domains_input, extra_aliases=extra_aliases)
    if expanded and required_aliases:
        required = expand_domains(required_aliases)
        for domain in required:
            if domain not in expanded:
                expanded.append(domain)
    return expanded


def _prepare_session_create(
    allowed_domains: list[str] | None,
    yolo: bool,
    claude_args: str | None,
    config_obj: PaudeConfig | None,
    agent_name: str = "claude",
    provider_name: str | None = None,
    otel_endpoint: str | None = None,
    composition: AgentComposition | None = None,
    credential_providers: list[str] | None = None,
) -> tuple[list[str], list[str], dict[str, str], bool]:
    """Shared pre-create logic for both backends.

    Returns:
        Tuple of (expanded_domains, parsed_args, env, unrestricted).
    """
    from paude.agents import get_agent
    from paude.domains import is_unrestricted

    parsed_args = _parse_agent_args(claude_args)

    if composition is None:
        agent_instance = get_agent(agent_name, provider=provider_name)
        agents = [agent_instance]
    else:
        agent_instance = composition.primary
        agents = composition.agents

    # Shared environments are merged with the primary agent taking
    # precedence when two toolchains define the same variable.
    env: dict[str, str] = {}
    for agent in reversed(agents):
        env.update(agent.build_environment())

    from paude.providers import get_provider

    for provider_name in credential_providers or []:
        provider_config = get_provider(provider_name)
        for var in provider_config.passthrough_env_vars:
            if var in os.environ:
                env.setdefault(var, os.environ[var])
        for prefix in provider_config.passthrough_env_prefixes:
            for key, value in os.environ.items():
                if key.startswith(prefix):
                    env.setdefault(key, value)
    extra_aliases: list[str] = []
    required_aliases: list[str] = []
    provider_aliases: list[str] = []
    for agent in agents:
        for alias in agent.config.extra_domain_aliases:
            if alias not in extra_aliases:
                extra_aliases.append(alias)
        for alias in agent.config.required_domain_aliases:
            if alias not in required_aliases:
                required_aliases.append(alias)
        aliases = _get_provider_aliases(agent.config.provider, agent.config.name)
        for alias in aliases or []:
            if alias not in provider_aliases:
                provider_aliases.append(alias)
    for provider_name in credential_providers or []:
        for alias in get_provider(provider_name).domain_aliases:
            if alias not in provider_aliases:
                provider_aliases.append(alias)

    expanded_domains = _expand_allowed_domains(
        allowed_domains,
        extra_aliases=extra_aliases,
        provider_aliases=provider_aliases,
        required_aliases=required_aliases,
    )

    # Inject OTEL env vars and auto-add endpoint hostname to allowed domains
    if otel_endpoint:
        from paude.otel import build_otel_env, parse_otel_endpoint

        env.update(build_otel_env(agent_name, otel_endpoint))
        hostname, _ = parse_otel_endpoint(otel_endpoint)
        if hostname not in expanded_domains:
            expanded_domains.append(hostname)

    # Resolve the host user's git identity so commits inside the container are
    # attributed correctly. Copying ~/.gitconfig alone misses identities kept
    # in XDG/system config or includeIf sections, so resolve it via git config
    # and pass it as env for the entrypoint to apply when the config lacks one.
    from paude.git_remote import resolve_local_git_identity

    git_name, git_email = resolve_local_git_identity()
    if git_name:
        env["PAUDE_GIT_USER_NAME"] = git_name
    if git_email:
        env["PAUDE_GIT_USER_EMAIL"] = git_email
    if not git_name and not git_email:
        typer.echo(
            "WARNING: No git identity found on the host. Commits inside the "
            "container will fail until you set user.name and user.email "
            "(git config --global user.name/user.email).",
            err=True,
        )

    unrestricted = is_unrestricted(expanded_domains)

    # Show warnings for dangerous configurations
    if yolo and unrestricted:
        typer.echo(
            "WARNING: Creating session with --yolo and unrestricted network.",
            err=True,
        )
        typer.echo(
            "         The agent can exfiltrate files without confirmation.",
            err=True,
        )
        typer.echo("", err=True)

    return expanded_domains, parsed_args, env, unrestricted


def _finalize_session_create(
    session: Session,
    expanded_domains: list[str],
    yolo: bool,
    git: bool,
    no_clone_origin: bool = False,
    ssh_host: str | None = None,
    ssh_key: str | None = None,
    remote_config_dir: str | None = None,
    paude_version: str | None = None,
) -> None:
    """Shared post-create output and git setup."""
    from paude.cli.remote_git_setup import _setup_git_after_create
    from paude.domains import format_domains_for_display
    from paude.registry import SessionRegistry

    SessionRegistry().register(
        session,
        ssh_host=ssh_host,
        ssh_key=ssh_key,
        remote_config_dir=remote_config_dir,
        paude_version=paude_version,
    )

    from paude.backends.naming import is_local_backend

    bt = session.backend_type
    status_msg = "created and running" if is_local_backend(bt) else "created"
    typer.echo(f"Session '{session.name}' {status_msg}.")
    domains_display = format_domains_for_display(expanded_domains)
    typer.echo(f"  Network: {domains_display}")
    if yolo:
        typer.echo("  Mode: YOLO (no permission prompts)")

    if git:
        _setup_git_after_create(
            session_name=session.name,
            backend_type=bt,
            no_clone_origin=no_clone_origin,
            ssh_host=ssh_host,
            ssh_key=ssh_key,
        )

    typer.echo("")
    if is_local_backend(bt):
        connect_hint = "To start working:"
    else:
        connect_hint = "Session is running. Connect with:"
    typer.echo(connect_hint)
    typer.echo(f"  paude connect {session.name}")


def _run_setup_command(backend: Backend, session_name: str, command: str) -> None:
    """Run a paude setup command in the session container."""
    typer.echo("Running setup command...", err=True)
    rc, stdout, stderr = backend.exec_in_session(
        session_name, f"cd /pvc/workspace && {command}"
    )
    if stdout:
        typer.echo(stdout.rstrip(), err=True)
    if stderr:
        typer.echo(stderr.rstrip(), err=True)
    if rc != 0:
        typer.echo(f"Warning: setup command failed (exit {rc})", err=True)
    else:
        typer.echo("Setup command completed.", err=True)


def _parse_copy_path(path_arg: str) -> tuple[str | None, str]:
    """Parse a copy path argument into (session_name, path).

    Returns:
        Tuple of (session_name, path) where session_name is:
        - None for local paths
        - "" for auto-detect (`:path` syntax)
        - session name for explicit (`session:path` syntax)
    """
    # Paths starting with / or . are always local
    if path_arg.startswith("/") or path_arg.startswith("."):
        return (None, path_arg)

    # Contains colon -> remote path
    if ":" in path_arg:
        session_part, path_part = path_arg.split(":", 1)
        return (session_part, path_part)

    # No colon, no / or . prefix -> local path
    return (None, path_arg)
