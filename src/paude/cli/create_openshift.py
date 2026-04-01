"""OpenShift backend session creation logic."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from paude.agents import get_agent
from paude.backends import SessionConfig, SessionExistsError
from paude.backends.openshift import (
    BuildFailedError,
    OpenShiftBackend,
    OpenShiftConfig,
    _generate_session_name,
)
from paude.cli.helpers import (
    _detect_dev_script_dir,
    _finalize_session_create,
    _run_post_create_command,
)
from paude.config.models import PaudeConfig
from paude.git_remote import is_git_repository


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
    provider_name: str | None = None,
    gpu: str | None = None,
    resources: dict[str, dict[str, str]] | None = None,
    build_resources: dict[str, dict[str, str]] | None = None,
    otel_ports: list[int] | None = None,
    otel_endpoint: str | None = None,
    secret_env_mapping: dict[str, str] | None = None,
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
        agent = get_agent(agent_name, provider=provider_name)
        image = os_backend.ensure_image_via_build(
            config=config,
            workspace=workspace,
            script_dir=os_script_dir,
            force_rebuild=rebuild,
            session_name=session_name,
            agent=agent,
        )

        # Build proxy image when running from source (ensures entrypoint.sh
        # stays in sync with CLI features like --otel-endpoint port injection).
        # os_script_dir is None for pip installs, which fall back to the
        # registry image via _resolve_proxy_image().
        proxy_image: str | None = None
        if not unrestricted and os_script_dir:
            typer.echo("Building proxy image in OpenShift cluster...")
            proxy_image = os_backend.ensure_proxy_image_via_build(
                script_dir=os_script_dir,
                force_rebuild=rebuild,
                session_name=session_name,
            )

        # Signal entrypoint to wait for git repo before launching Claude
        if git:
            if is_git_repository():
                env["PAUDE_WAIT_FOR_GIT"] = "1"
            else:
                typer.echo(
                    "Warning: Not in a git repository. Skipping --git setup.",
                    err=True,
                )
                git = False

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
            provider=provider_name,
            gpu=gpu,
            ports=agent.config.exposed_ports,
            otel_ports=otel_ports or [],
            otel_endpoint=otel_endpoint,
            secret_env_mapping=secret_env_mapping or {},
        )

        session = os_backend.create_session(session_config)
    except BuildFailedError as e:
        typer.echo(f"Build failed: {e}", err=True)
        raise typer.Exit(1) from None
    except SessionExistsError as e:
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

    if config and config.post_create_command:
        _run_post_create_command(os_backend, session.name, config.post_create_command)

    # Start the agent after git push so wait_for_git finds .git immediately
    os_backend.start_agent_headless(session.name)
