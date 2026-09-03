"""Podman/Docker backend session creation logic."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from paude.agents import get_agents
from paude.backends import PodmanBackend, SessionConfig, SessionExistsError
from paude.backends.labels import SessionSpec
from paude.backends.podman.helpers import agent_specs_for
from paude.cli.helpers import (
    _finalize_session_create,
    _run_setup_command,
)
from paude.cli.session_rebuild import (
    ImageBuildError,
    build_session_images,
    prepare_session_mounts,
    session_config_from_spec,
)
from paude.config.models import PaudeConfig
from paude.subprocess_utils import called_process_stderr

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.backends.base import Session
    from paude.container.engine import ContainerEngine
    from paude.transport.base import Transport
    from paude.transport.config_sync import RemoteConfigPaths


def create_podman_session(
    *,
    name: str | None,
    workspace: Path,
    config: PaudeConfig | None,
    env: dict[str, str],
    expanded_domains: list[str],
    allowed_endpoints: list[str] | None = None,
    parsed_args: list[str],
    yolo: bool,
    git: bool,
    no_clone_origin: bool = False,
    rebuild: bool,
    platform: str | None,
    agent_name: str = "claude",
    provider_name: str | None = None,
    agent_providers: list[tuple[str, str]] | None = None,
    credential_providers: list[str] | None = None,
    engine_binary: str = "podman",
    ssh_host: str | None = None,
    ssh_key: str | None = None,
    transport: Transport | None = None,
    gpu: str | None = None,
    otel_ports: list[int] | None = None,
    otel_endpoint: str | None = None,
) -> None:
    """Local container session creation logic (Podman or Docker)."""
    from paude.container.engine import ContainerEngine

    engine = ContainerEngine(engine_binary, transport=transport)
    composition, spec = _resolve_composition_and_spec(
        agent_name=agent_name,
        provider_name=provider_name,
        agent_providers=agent_providers,
        credential_providers=credential_providers,
        gpu=gpu,
        yolo=yolo,
        otel_endpoint=otel_endpoint,
        allowed_endpoints=allowed_endpoints or [],
    )

    try:
        images = build_session_images(
            engine=engine,
            composition=composition,
            config=config,
            workspace=workspace,
            force_rebuild=rebuild,
            platform=platform,
        )
    except ImageBuildError as e:
        label = "image" if e.stage == "agent" else "proxy image"
        typer.echo(f"Error ensuring {label}: {e.cause}", err=True)
        raise typer.Exit(1) from None

    prepared = prepare_session_mounts(engine=engine, composition=composition)

    session_config = session_config_from_spec(
        spec,
        name=name,
        workspace=workspace,
        composition=composition,
        images=images,
        env=env,
        mounts=prepared.mounts,
        expanded_domains=expanded_domains,
        otel_ports=otel_ports or [],
        args=parsed_args,
        workdir=str(workspace),
    )

    backend_instance, session = _create_session_or_exit(
        engine, session_config, prepared.remote_config
    )

    from paude import __version__

    _finalize_session_create(
        session=session,
        expanded_domains=expanded_domains,
        yolo=yolo,
        git=git,
        no_clone_origin=no_clone_origin,
        ssh_host=ssh_host,
        ssh_key=ssh_key,
        remote_config_dir=(
            prepared.remote_config.remote_base if prepared.remote_config else None
        ),
        paude_version=__version__,
    )

    if config and config.setup_command:
        _run_setup_command(backend_instance, session.name, config.setup_command)


def _resolve_composition_and_spec(
    *,
    agent_name: str,
    provider_name: str | None,
    agent_providers: list[tuple[str, str]] | None,
    credential_providers: list[str] | None,
    gpu: str | None,
    yolo: bool,
    otel_endpoint: str | None,
    allowed_endpoints: list[str] | None = None,
) -> tuple[AgentComposition, SessionSpec]:
    """Resolve the requested agents and gather the session's declared config.

    The spec is normalised here rather than downstream: unspecified credential
    providers mean "whatever the agents map to", and that default belongs with
    the caller that knows it, not inside the shared session builder.
    """
    specs = agent_providers or [(agent_name, provider_name or "")]
    composition = get_agents(
        [name for name, _provider in specs],
        providers={name: provider for name, provider in specs if provider},
        include_bundled=False,
    )
    resolved_specs = agent_specs_for(composition)
    spec = SessionSpec(
        agent=agent_name,
        provider=provider_name,
        agent_providers=resolved_specs,
        credential_providers=credential_providers
        or [provider for _agent, provider in resolved_specs],
        gpu=gpu,
        yolo=yolo,
        otel_endpoint=otel_endpoint,
        allowed_endpoints=allowed_endpoints or [],
    )
    return composition, spec


def _create_session_or_exit(
    engine: ContainerEngine,
    session_config: SessionConfig,
    remote_config: RemoteConfigPaths | None,
) -> tuple[PodmanBackend, Session]:
    """Create and start the session, or report the failure and exit.

    An existing session is reported as-is and deliberately left alone; every
    other failure is rolled back on a best-effort basis, including any config
    files synced to a remote host.
    """
    backend_instance = PodmanBackend(engine=engine)
    try:
        session = backend_instance.create_session(session_config)
        # Auto-start the container (entrypoint is tini -- sleep infinity)
        backend_instance.start_session_no_attach(session.name)
    except SessionExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error creating session: {e}", err=True)
        if detail := called_process_stderr(e):
            typer.echo(detail, err=True)
        try:
            backend_instance.delete_session(session.name, confirm=True)
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        if remote_config:
            try:
                from paude.transport.config_sync import cleanup_remote_configs
                from paude.transport.ssh import SshTransport

                if isinstance(engine.transport, SshTransport):
                    cleanup_remote_configs(engine.transport, remote_config.remote_base)
            except Exception:  # noqa: S110 - best-effort cleanup
                pass
        raise typer.Exit(1) from None
    return backend_instance, session
