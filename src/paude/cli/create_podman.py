"""Podman/Docker backend session creation logic."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from paude.agents import get_agent
from paude.backends import PodmanBackend, SessionConfig, SessionExistsError
from paude.cli.helpers import (
    _detect_dev_script_dir,
    _finalize_session_create,
)
from paude.config.models import PaudeConfig

if TYPE_CHECKING:
    from paude.transport.base import Transport


def create_podman_session(
    *,
    name: str | None,
    workspace: Path,
    config: PaudeConfig | None,
    env: dict[str, str],
    expanded_domains: list[str] | None,
    unrestricted: bool,
    parsed_args: list[str],
    yolo: bool,
    git: bool,
    no_clone_origin: bool = False,
    rebuild: bool,
    platform: str | None,
    agent_name: str = "claude",
    engine_binary: str = "podman",
    ssh_host: str | None = None,
    ssh_key: str | None = None,
    transport: Transport | None = None,
    gpu: str | None = None,
) -> None:
    """Local container session creation logic (Podman or Docker)."""
    from paude.container import ImageManager
    from paude.container.engine import ContainerEngine
    from paude.mounts import build_mounts

    engine = ContainerEngine(engine_binary, transport=transport)
    home = Path.home()
    agent_instance = get_agent(agent_name)
    image_manager = ImageManager(
        script_dir=_detect_dev_script_dir(),
        platform=platform,
        agent=agent_instance,
        engine=engine,
    )

    # Ensure image
    try:
        has_custom = config and (config.base_image or config.dockerfile)
        if has_custom and config is not None:
            image = image_manager.ensure_custom_image(
                config, force_rebuild=rebuild, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image()
    except Exception as e:
        typer.echo(f"Error ensuring image: {e}", err=True)
        raise typer.Exit(1) from None

    # Build mounts
    mounts = build_mounts(home, agent_instance)

    # Sync configs to remote host if using SSH
    remote_config_paths = None
    if engine.is_remote:
        from paude.transport.config_sync import remap_mounts, sync_configs_to_remote
        from paude.transport.ssh import SshTransport

        if isinstance(engine.transport, SshTransport):
            typer.echo("Syncing configuration to remote host...", err=True)
            remote_config_paths = sync_configs_to_remote(engine.transport, mounts)
            mounts = remap_mounts(mounts, remote_config_paths.path_map)

    # Ensure proxy image when domain filtering is active
    podman_proxy_image: str | None = None
    if not unrestricted:
        try:
            podman_proxy_image = image_manager.ensure_proxy_image(force_rebuild=rebuild)
        except Exception as e:
            typer.echo(f"Error ensuring proxy image: {e}", err=True)
            raise typer.Exit(1) from None

    # Create session config
    session_config = SessionConfig(
        name=name,
        workspace=workspace,
        image=image,
        env=env,
        mounts=mounts,
        args=parsed_args,
        workdir=str(workspace),
        allowed_domains=expanded_domains,
        yolo=yolo,
        proxy_image=podman_proxy_image,
        agent=agent_name,
        gpu=gpu,
    )

    try:
        backend_instance = PodmanBackend(engine=engine)
        session = backend_instance.create_session(session_config)

        # Auto-start the container (entrypoint is sleep infinity)
        backend_instance.start_session_no_attach(session.name)
    except SessionExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error creating session: {e}", err=True)
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            typer.echo(e.stderr.strip(), err=True)
        try:
            backend_instance.delete_session(session.name, confirm=True)
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        if remote_config_paths:
            try:
                from paude.transport.config_sync import cleanup_remote_configs
                from paude.transport.ssh import SshTransport

                if isinstance(engine.transport, SshTransport):
                    cleanup_remote_configs(
                        engine.transport, remote_config_paths.remote_base
                    )
            except Exception:  # noqa: S110 - best-effort cleanup
                pass
        raise typer.Exit(1) from None

    _finalize_session_create(
        session=session,
        expanded_domains=expanded_domains,
        yolo=yolo,
        git=git,
        no_clone_origin=no_clone_origin,
        ssh_host=ssh_host,
        ssh_key=ssh_key,
        remote_config_dir=(
            remote_config_paths.remote_base if remote_config_paths else None
        ),
    )
