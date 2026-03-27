"""OpenShift backend session creation logic."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer

from paude.agents import get_agent
from paude.backends import SessionConfig
from paude.backends.openshift import (
    BuildFailedError,
    OpenShiftBackend,
    OpenShiftConfig,
    _generate_session_name,
)
from paude.backends.openshift import (
    SessionExistsError as OpenshiftSessionExistsError,
)
from paude.cli.helpers import (
    _detect_dev_script_dir,
    _finalize_session_create,
)
from paude.config.models import PaudeConfig


def create_openshift_session(
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
    pvc_size: str,
    storage_class: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
    credential_timeout: int,
    agent_name: str = "claude",
    gpu: str | None = None,
    resources: dict[str, dict[str, str]] | None = None,
    build_resources: dict[str, dict[str, str]] | None = None,
) -> None:
    """OpenShift-specific session creation logic."""
    os_script_dir = _detect_dev_script_dir()

    openshift_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
        **({"resources": resources} if resources is not None else {}),
        **({"build_resources": build_resources} if build_resources is not None else {}),
    )

    os_backend = OpenShiftBackend(config=openshift_config)

    # Pre-compute session name for labeling builds
    session_name = name if name else _generate_session_name(workspace)

    try:
        # Build image via OpenShift binary build
        typer.echo("Building image in OpenShift cluster...")
        image = os_backend.ensure_image_via_build(
            config=config,
            workspace=workspace,
            script_dir=os_script_dir,
            force_rebuild=rebuild,
            session_name=session_name,
            agent=get_agent(agent_name),
        )

        # Build proxy image when needed (PAUDE_DEV=1 and proxy is used)
        proxy_image: str | None = None
        if not unrestricted:
            dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
            if dev_mode and os_script_dir:
                typer.echo("Building proxy image in OpenShift cluster...")
                proxy_image = os_backend.ensure_proxy_image_via_build(
                    script_dir=os_script_dir,
                    force_rebuild=rebuild,
                    session_name=session_name,
                )

        # Signal entrypoint to wait for git repo before launching Claude
        if git:
            env["PAUDE_WAIT_FOR_GIT"] = "1"

        # Create session config
        session_config = SessionConfig(
            name=session_name,
            workspace=workspace,
            image=image,
            env=env,
            mounts=[],  # OpenShift uses oc rsync, not mounts
            args=parsed_args,
            workdir=str(workspace),
            allowed_domains=expanded_domains,
            yolo=yolo,
            pvc_size=pvc_size,
            storage_class=storage_class,
            proxy_image=proxy_image,
            credential_timeout=credential_timeout,
            agent=agent_name,
            gpu=gpu,
        )

        session = os_backend.create_session(session_config)
    except BuildFailedError as e:
        typer.echo(f"Build failed: {e}", err=True)
        raise typer.Exit(1) from None
    except OpenshiftSessionExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error creating session: {e}", err=True)
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            typer.echo(e.stderr.strip(), err=True)
        try:
            os_backend.delete_session(session_name, confirm=True)
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        raise typer.Exit(1) from None

    from paude import __version__

    _finalize_session_create(
        session=session,
        expanded_domains=expanded_domains,
        yolo=yolo,
        git=git,
        openshift_context=openshift_context,
        openshift_namespace=os_backend.namespace,
        no_clone_origin=no_clone_origin,
        paude_version=__version__,
    )
