"""Shared helper functions for CLI commands."""

from __future__ import annotations

from pathlib import Path

import typer

from paude.backends import PodmanBackend
from paude.backends.base import Backend, Session
from paude.backends.openshift import OpenShiftBackend, OpenShiftConfig
from paude.cli.app import BackendType
from paude.config.models import PaudeConfig
from paude.session_discovery import (
    collect_all_sessions,
    create_openshift_backend,
    find_workspace_session,
)


def find_session_backend(
    session_name: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> tuple[BackendType, Backend] | None:
    """Find which backend contains the given session.

    Args:
        session_name: Name of the session to find.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Tuple of (backend_type, backend_instance) if found, None otherwise.
        The backend_instance is either PodmanBackend or OpenShiftBackend.
    """
    # Try Podman first
    try:
        podman = PodmanBackend()
        if podman.get_session(session_name) is not None:
            return (BackendType.podman, podman)
    except Exception:  # noqa: S110 - Podman may not be available
        pass

    # Try OpenShift
    os_backend = create_openshift_backend(openshift_context, openshift_namespace)
    if os_backend is not None:
        try:
            if os_backend.get_session(session_name) is not None:
                return (BackendType.openshift, os_backend)
        except Exception:  # noqa: S110
            pass

    return None


def _get_backend_instance(
    backend: BackendType,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
) -> Backend:
    """Create a backend instance based on the backend type.

    Args:
        backend: The backend type to create.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Backend instance (PodmanBackend or OpenShiftBackend).
    """
    if backend == BackendType.podman:
        return PodmanBackend()
    openshift_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
    )
    return OpenShiftBackend(config=openshift_config)


def _auto_select_session(
    openshift_context: str | None,
    openshift_namespace: str | None,
    *,
    status_filter: str | None = None,
    no_sessions_hints: list[str],
    multi_hint_format: str = "  paude start {name}  # {backend_type}",
) -> tuple[Session, Backend]:
    """Auto-select a session when no name/backend is specified.

    Searches workspace sessions first, then all sessions. Exits with
    code 1 if no sessions found or multiple sessions found.

    Args:
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.
        status_filter: Optional status filter (e.g. "running").
        no_sessions_hints: Messages to show when no sessions found.
        multi_hint_format: Format string for each session in multi-session
            list. Available placeholders: {name}, {backend_type}, {status},
            {workspace}.

    Returns:
        Tuple of (session, backend) for the selected session.
    """
    workspace_match = find_workspace_session(
        openshift_context, openshift_namespace, status_filter=status_filter
    )
    if workspace_match:
        return workspace_match

    all_sessions = collect_all_sessions(
        openshift_context, openshift_namespace, status_filter=status_filter
    )
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


def _expand_allowed_domains(
    allowed_domains: list[str] | None,
    extra_aliases: list[str] | None = None,
) -> list[str] | None:
    """Expand domain aliases, defaulting to ["default"].

    Args:
        allowed_domains: Raw domain list from CLI, or None for defaults.
        extra_aliases: Agent-specific aliases to add on top of BASE_ALIASES
            when expanding "default". If None, falls back to DEFAULT_ALIASES.
    """
    from paude.domains import expand_domains

    domains_input = allowed_domains if allowed_domains else ["default"]
    return expand_domains(domains_input, extra_aliases=extra_aliases)


def _prepare_session_create(
    allowed_domains: list[str] | None,
    yolo: bool,
    claude_args: str | None,
    config_obj: PaudeConfig | None,
    agent_name: str = "claude",
) -> tuple[list[str] | None, list[str], dict[str, str], bool]:
    """Shared pre-create logic for both backends.

    Returns:
        Tuple of (expanded_domains, parsed_args, env, unrestricted).
    """
    from paude.agents import get_agent
    from paude.domains import is_unrestricted

    parsed_args = _parse_agent_args(claude_args)

    # Build environment from agent
    agent_instance = get_agent(agent_name)
    env = agent_instance.build_environment()
    if config_obj and config_obj.container_env:
        env.update(config_obj.container_env)

    expanded_domains = _expand_allowed_domains(
        allowed_domains, extra_aliases=agent_instance.config.extra_domain_aliases
    )
    unrestricted = is_unrestricted(expanded_domains)

    # Show warnings for dangerous configurations
    if yolo and unrestricted:
        typer.echo(
            "WARNING: Creating session with --yolo and unrestricted network.",
            err=True,
        )
        typer.echo(
            "         Claude can exfiltrate files without confirmation.",
            err=True,
        )
        typer.echo("", err=True)

    return expanded_domains, parsed_args, env, unrestricted


def _finalize_session_create(
    session: Session,
    expanded_domains: list[str] | None,
    yolo: bool,
    git: bool,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    no_clone_origin: bool = False,
) -> None:
    """Shared post-create output and git setup."""
    from paude.cli.remote import _setup_git_after_create
    from paude.domains import format_domains_for_display

    bt = session.backend_type
    status_msg = "created and running" if bt == "podman" else "created"
    typer.echo(f"Session '{session.name}' {status_msg}.")
    domains_display = format_domains_for_display(expanded_domains)
    typer.echo(f"  Network: {domains_display}")
    if yolo:
        typer.echo("  Mode: YOLO (no permission prompts)")

    if git:
        _setup_git_after_create(
            session_name=session.name,
            backend_type=bt,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
            no_clone_origin=no_clone_origin,
        )

    typer.echo("")
    if bt == "podman":
        connect_hint = "To start working:"
    else:
        connect_hint = "Session is running. Connect with:"
    typer.echo(connect_hint)
    typer.echo(f"  paude connect {session.name}")


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
