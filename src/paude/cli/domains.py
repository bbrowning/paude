"""Domain management commands: allowed-domains and blocked-domains."""

from __future__ import annotations

from typing import Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.base import Backend
from paude.backends.openshift import (
    SessionNotFoundError as OpenshiftSessionNotFoundError,
)
from paude.cli.app import BackendType, app
from paude.cli.helpers import _get_backend_instance, find_session_backend
from paude.proxy_log import parse_blocked_log


def _resolve_backend_for_domains(
    name: str,
    backend: BackendType | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
) -> Backend:
    """Resolve the backend instance for allowed-domains command.

    Args:
        name: Session name.
        backend: Explicit backend type, or None for auto-detect.
        openshift_context: Optional OpenShift context.
        openshift_namespace: Optional OpenShift namespace.

    Returns:
        Backend instance.

    Raises:
        typer.Exit: If session not found or backend not supported.
    """
    if backend is None:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        _, backend_obj = result
        return backend_obj

    return _get_backend_instance(backend, openshift_context, openshift_namespace)


def _check_domains_mutual_exclusivity(
    add: list[str] | None,
    remove: list[str] | None,
    replace: list[str] | None,
) -> None:
    """Check that at most one of --add, --remove, --replace is specified.

    Raises:
        typer.Exit: If more than one is specified.
    """
    specified = sum(1 for opt in (add, remove, replace) if opt)
    if specified > 1:
        typer.echo(
            "Error: Only one of --add, --remove, --replace can be specified.",
            err=True,
        )
        raise typer.Exit(1)


def _expand_domains_or_exit(domains: list[str]) -> list[str]:
    """Expand domains and exit if 'all' is used.

    Args:
        domains: Raw domain input list.

    Returns:
        Expanded domain list.

    Raises:
        typer.Exit: If 'all' is specified.
    """
    from paude.domains import expand_domains

    expanded = expand_domains(domains)
    if expanded is None:
        typer.echo(
            "Error: 'all' cannot be used with --add, --remove, or --replace.",
            err=True,
        )
        raise typer.Exit(1)
    return expanded


def _list_domains(backend_obj: Backend, name: str) -> None:
    """List current allowed domains for a session.

    Args:
        backend_obj: Backend instance.
        name: Session name.
    """
    from paude.domains import format_domains_for_display

    domains = backend_obj.get_allowed_domains(name)
    summary = format_domains_for_display(domains)
    typer.echo(f"Network: {summary}")
    if domains is not None:
        typer.echo("")
        for domain in domains:
            typer.echo(f"  {domain}")


def _add_domains(backend_obj: Backend, name: str, add: list[str]) -> None:
    """Add domains to the current allowed list.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        add: Domains to add.
    """
    expanded = _expand_domains_or_exit(add)
    current = backend_obj.get_allowed_domains(name)
    if current is None:
        typer.echo(
            "Error: Session has unrestricted network (no proxy). Cannot add domains.",
            err=True,
        )
        raise typer.Exit(1)

    # Merge with dedup, preserving order
    seen = set(current)
    merged = list(current)
    for d in expanded:
        if d not in seen:
            merged.append(d)
            seen.add(d)

    backend_obj.update_allowed_domains(name, merged)
    added_count = len(merged) - len(current)
    typer.echo(f"Added {added_count} domain(s) to session '{name}'.")


def _remove_domains(backend_obj: Backend, name: str, remove: list[str]) -> None:
    """Remove domains from the current allowed list.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        remove: Domains to remove.
    """
    expanded = _expand_domains_or_exit(remove)
    current = backend_obj.get_allowed_domains(name)
    if current is None:
        typer.echo(
            "Error: Session has unrestricted network (no proxy). "
            "Cannot remove domains.",
            err=True,
        )
        raise typer.Exit(1)

    remove_set = set(expanded)
    remaining = [d for d in current if d not in remove_set]

    if not remaining:
        typer.echo(
            "Error: Cannot remove all domains. At least one domain must remain.",
            err=True,
        )
        raise typer.Exit(1)

    backend_obj.update_allowed_domains(name, remaining)
    removed_count = len(current) - len(remaining)
    typer.echo(f"Removed {removed_count} domain(s) from session '{name}'.")


def _replace_domains(backend_obj: Backend, name: str, replace: list[str]) -> None:
    """Replace all domains for a session.

    Args:
        backend_obj: Backend instance.
        name: Session name.
        replace: New domain list.
    """
    expanded = _expand_domains_or_exit(replace)
    backend_obj.update_allowed_domains(name, expanded)
    typer.echo(f"Replaced domains for session '{name}' ({len(expanded)} domain(s)).")


@app.command("allowed-domains")
def allowed_domains_cmd(
    name: Annotated[str, typer.Argument(help="Session name.")],
    add: Annotated[
        list[str] | None,
        typer.Option("--add", help="Add domains to current list."),
    ] = None,
    remove: Annotated[
        list[str] | None,
        typer.Option("--remove", help="Remove domains from current list."),
    ] = None,
    replace: Annotated[
        list[str] | None,
        typer.Option("--replace", help="Replace entire domain list."),
    ] = None,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Manage allowed egress domains for a session."""
    _check_domains_mutual_exclusivity(add, remove, replace)

    backend_obj = _resolve_backend_for_domains(
        name, backend, openshift_context, openshift_namespace
    )

    try:
        if add:
            _add_domains(backend_obj, name, add)
        elif remove:
            _remove_domains(backend_obj, name, remove)
        elif replace:
            _replace_domains(backend_obj, name, replace)
        else:
            _list_domains(backend_obj, name)
    except NotImplementedError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except (OpenshiftSessionNotFoundError, SessionNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error managing domains: {e}", err=True)
        raise typer.Exit(1) from None


@app.command("blocked-domains")
def blocked_domains_cmd(
    name: Annotated[str, typer.Argument(help="Session name.")],
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Show raw proxy log instead of parsed summary."),
    ] = False,
    backend: Annotated[
        BackendType | None,
        typer.Option(
            "--backend",
            help="Container backend (auto-detected from session if not specified).",
        ),
    ] = None,
    openshift_context: Annotated[
        str | None,
        typer.Option(
            "--openshift-context",
            help="Kubeconfig context for OpenShift.",
        ),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Show domains blocked by the proxy for a session."""
    backend_obj = _resolve_backend_for_domains(
        name, backend, openshift_context, openshift_namespace
    )

    try:
        log_content = backend_obj.get_proxy_blocked_log(name)
    except (OpenshiftSessionNotFoundError, SessionNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error reading blocked domains: {e}", err=True)
        raise typer.Exit(1) from None

    if log_content is None:
        typer.echo(
            f"Session '{name}' has unrestricted network (no proxy). "
            "No domains are blocked."
        )
        return

    if raw:
        if not log_content.strip():
            typer.echo(f"No blocked domains for session '{name}'.")
            return
        typer.echo(log_content, nl=False)
        return

    entries = parse_blocked_log(log_content)
    if not entries:
        typer.echo(f"No blocked domains for session '{name}'.")
        return

    total_requests = sum(e.count for e in entries)
    max_domain_len = max(len(e.domain) for e in entries)

    typer.echo(f"Blocked domains for session '{name}':")
    typer.echo("")
    for entry in entries:
        label = "request" if entry.count == 1 else "requests"
        typer.echo(f"  {entry.domain:<{max_domain_len}}  {entry.count:>4} {label}")
    typer.echo("")
    typer.echo(
        f"{len(entries)} unique domain(s) blocked ({total_requests} total requests)."
    )
    typer.echo("")
    typer.echo("Tip: To allow a domain, run:")
    typer.echo(f"  paude allowed-domains {name} --add <domain>")
