"""Git setup after session creation: clone, push, and configure origin."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from paude.backends.shared import (
    engine_binary_for_backend,
    is_local_backend,
    pod_name,
    resource_name,
)

if TYPE_CHECKING:
    from paude.backends.base import Session
    from paude.git_remote.exec_cmd import ExecCmdBuilder
    from paude.transport.base import Transport


@dataclass(frozen=True)
class GitSetupContext:
    """Bundle of parameters shared across git setup functions."""

    session_name: str
    backend_type: str
    openshift_context: str | None
    openshift_namespace: str | None
    transport: Transport | None = None

    def make_exec_context(self) -> tuple[ExecCmdBuilder, Transport | None]:
        """Build an exec builder and resolve transport.

        For local backends, returns a podman exec builder with transport passed through.
        For OpenShift backends, returns an openshift exec builder with transport=None
        (OpenShift exec goes through the API server, not SSH).
        """
        from paude.git_remote import openshift_exec_builder, podman_exec_builder

        if is_local_backend(self.backend_type):
            eb = podman_exec_builder(
                resource_name(self.session_name),
                engine_binary_for_backend(self.backend_type),
            )
            return eb, self.transport
        eb = openshift_exec_builder(
            pod_name(self.session_name),
            self.openshift_namespace or "default",
            self.openshift_context,
        )
        return eb, None

    @classmethod
    def from_session(
        cls,
        session: Session,
        openshift_context: str | None,
        openshift_namespace: str | None,
        transport: Transport | None = None,
    ) -> GitSetupContext:
        """Create from a Session object."""
        return cls(
            session_name=session.name,
            backend_type=session.backend_type,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
            transport=transport,
        )


def _build_transport(
    ssh_host: str | None, ssh_key: str | None = None
) -> Transport | None:
    """Create an SSH transport if ssh_host is set, otherwise return None."""
    if not ssh_host:
        return None
    from paude.transport.ssh import SshTransport, parse_ssh_host

    host, port = parse_ssh_host(ssh_host)
    return SshTransport(host, key=ssh_key, port=port)


def _setup_git_after_create(
    session_name: str,
    backend_type: str,
    openshift_context: str | None = None,
    openshift_namespace: str | None = None,
    no_clone_origin: bool = False,
    ssh_host: str | None = None,
    ssh_key: str | None = None,
) -> bool:
    """Set up git remote, push code and tags, and configure origin after create.

    When an origin URL exists and no_clone_origin is False, attempts to clone
    from origin inside the container (fast datacenter bandwidth), then pushes
    only local-only commits as a delta. Falls back to full push if clone fails.

    Returns:
        True if all steps succeeded, False if any step failed.
    """
    from paude.git_remote import (
        get_current_branch,
        get_upstream_url,
        is_git_repository,
        ssh_url_to_https,
    )

    transport = _build_transport(ssh_host, ssh_key)

    if not is_git_repository():
        typer.echo(
            "Warning: Not in a git repository. Skipping --git setup.",
            err=True,
        )
        return False

    typer.echo("")
    typer.echo("Setting up git...")

    branch = get_current_branch()
    if branch == "HEAD":
        branch = None

    origin_url = get_upstream_url()
    origin_https_url = ssh_url_to_https(origin_url) if origin_url else None

    ctx = GitSetupContext(
        session_name=session_name,
        backend_type=backend_type,
        openshift_context=openshift_context,
        openshift_namespace=openshift_namespace,
        transport=transport,
    )

    cloned = False
    if origin_https_url and branch and not no_clone_origin:
        cloned = _try_clone_from_origin(ctx, origin_https_url)

    if cloned:
        _setup_after_clone(ctx, branch or "main")
    else:
        _setup_full_push(ctx, branch or "main", origin_https_url)

    _setup_precommit(ctx)

    typer.echo("Git setup complete.")
    return True


def _try_clone_from_origin(
    ctx: GitSetupContext,
    origin_https_url: str,
) -> bool:
    """Try to clone from origin inside the container. Returns True on success."""
    from paude.git_remote import clone_from_origin

    typer.echo(f"Cloning from origin in container ({origin_https_url})...")

    eb, tp = ctx.make_exec_context()
    success = clone_from_origin(eb, origin_https_url, transport=tp)

    if not success:
        typer.echo(
            "Clone from origin failed (private repo or network issue). "
            "Falling back to full push.",
        )
    return success


def _setup_after_clone(
    ctx: GitSetupContext,
    branch: str,
) -> None:
    """Post-clone setup: add ext:: remote, push delta, set base ref."""
    from paude.cli.remote import _remote_add
    from paude.git_remote import (
        count_local_only_commits,
        git_push_to_remote,
        set_base_ref_in_container,
    )

    _remote_add(
        name=ctx.session_name,
        openshift_context=ctx.openshift_context,
        openshift_namespace=ctx.openshift_namespace,
        push=False,
        transport=ctx.transport,
    )

    rname = resource_name(ctx.session_name)
    local_count = count_local_only_commits(branch)

    if local_count is None or local_count > 0:
        if local_count is not None:
            plural = "commit" if local_count == 1 else "commits"
            n_desc = f"{local_count} local {plural}"
        else:
            n_desc = "local commits"
        typer.echo(f"Pushing {n_desc} to container...")
        if not git_push_to_remote(rname, branch, quiet=True):
            if local_count is not None:
                typer.echo(
                    "  Note: Could not push local commits (branch has diverged "
                    "from origin). Container has latest origin code.",
                )

    eb, tp = ctx.make_exec_context()
    set_base_ref_in_container(eb, transport=tp)


def _setup_full_push(
    ctx: GitSetupContext,
    branch: str,
    origin_https_url: str | None,
) -> None:
    """Original full-push flow: init, push all, set origin."""
    from paude.cli.remote import _remote_add
    from paude.git_remote import (
        git_push_tags_to_remote,
        git_push_to_remote,
        set_base_ref_in_container,
        set_origin_in_container,
    )

    _remote_add(
        name=ctx.session_name,
        openshift_context=ctx.openshift_context,
        openshift_namespace=ctx.openshift_namespace,
        push=False,
        transport=ctx.transport,
    )

    rname = resource_name(ctx.session_name)
    typer.echo(f"Pushing {branch} to container...")
    if not git_push_to_remote(rname, branch):
        typer.echo("Warning: Failed to push branch.", err=True)
        return

    eb, tp = ctx.make_exec_context()
    set_base_ref_in_container(eb, transport=tp)

    typer.echo("Pushing tags...")
    if not git_push_tags_to_remote(rname):
        typer.echo("Warning: Failed to push tags.", err=True)

    if origin_https_url:
        typer.echo(f"Setting origin in container to {origin_https_url}...")
        origin_set = set_origin_in_container(eb, origin_https_url, transport=tp)
        if not origin_set:
            typer.echo("Warning: Failed to set origin in container.", err=True)
    else:
        typer.echo("No local origin remote found. Skipping origin setup in container.")


def _setup_precommit(ctx: GitSetupContext) -> None:
    """Set up pre-commit hooks if config exists."""
    from paude.git_remote import setup_precommit_in_container

    if not Path(".pre-commit-config.yaml").exists():
        return

    typer.echo("Setting up pre-commit hooks in container...")
    eb, tp = ctx.make_exec_context()
    success = setup_precommit_in_container(
        eb, set_home=not is_local_backend(ctx.backend_type), transport=tp
    )
    if not success:
        typer.echo(
            "Warning: Failed to install pre-commit hooks in container.",
            err=True,
        )


def _push_after_add(
    session: Session,
    rname: str,
    branch: str,
    openshift_context: str | None,
    openshift_namespace: str | None,
    transport: Transport | None,
) -> None:
    """Push branch and set base ref after adding a remote."""
    from paude.git_remote import git_push_to_remote, set_base_ref_in_container

    typer.echo("")
    typer.echo(f"Pushing {branch} to container...")
    if not git_push_to_remote(rname, branch):
        typer.echo("Push failed.", err=True)
        raise typer.Exit(1)

    ctx = GitSetupContext.from_session(
        session, openshift_context, openshift_namespace, transport
    )
    eb, tp = ctx.make_exec_context()
    set_base_ref_in_container(eb, transport=tp)
    typer.echo("Push complete.")
