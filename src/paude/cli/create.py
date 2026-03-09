"""Session create command and backend-specific creation logic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from paude.backends import (
    PodmanBackend,
    SessionConfig,
    SessionExistsError,
)
from paude.backends.openshift import (
    BuildFailedError,
    OpenShiftBackend,
    OpenShiftConfig,
)
from paude.backends.openshift import (
    SessionExistsError as OpenshiftSessionExistsError,
)
from paude.cli.app import BackendType, app
from paude.cli.helpers import (
    _detect_dev_script_dir,
    _expand_allowed_domains,
    _finalize_session_create,
    _parse_claude_args,
    _prepare_session_create,
)
from paude.config.models import PaudeConfig


@app.command("create")
def session_create(
    name: Annotated[
        str | None,
        typer.Argument(help="Session name (auto-generated if not specified)"),
    ] = None,
    backend: Annotated[
        BackendType,
        typer.Option(
            "--backend",
            help="Container backend to use.",
        ),
    ] = BackendType.podman,
    yolo: Annotated[
        bool,
        typer.Option(
            "--yolo",
            help="Enable YOLO mode (skip all permission prompts).",
        ),
    ] = False,
    allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domains",
            help=(
                "Domains to allow network access. Can be repeated. "
                "Special values: 'all' (unrestricted), "
                "'default' (vertexai+python+github), "
                "'vertexai', 'python', 'golang', 'nodejs', "
                "'rust'. Default: 'default'."
            ),
        ),
    ] = None,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Force rebuild of workspace container image.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Show configuration and what would be done, then exit.",
        ),
    ] = False,
    claude_args: Annotated[
        str | None,
        typer.Option(
            "--args",
            "-a",
            help="Arguments to pass to claude (e.g., -a '-p \"prompt\"').",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose output (affects --dry-run display).",
        ),
    ] = False,
    pvc_size: Annotated[
        str,
        typer.Option(
            "--pvc-size",
            help="PVC size for OpenShift (e.g., 10Gi).",
        ),
    ] = "10Gi",
    storage_class: Annotated[
        str | None,
        typer.Option(
            "--storage-class",
            help="Storage class for OpenShift.",
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
    platform: Annotated[
        str | None,
        typer.Option(
            "--platform",
            help="Target platform for image builds (e.g., linux/amd64, linux/arm64).",
        ),
    ] = None,
    credential_timeout: Annotated[
        int,
        typer.Option(
            "--credential-timeout",
            help="Inactivity minutes before removing credentials (OpenShift).",
        ),
    ] = 60,
    git: Annotated[
        bool,
        typer.Option(
            "--git",
            help="Set up git remote, push code+tags, configure origin.",
        ),
    ] = False,
) -> None:
    """Create a new persistent session (does not start it)."""
    # Handle dry-run mode
    if dry_run:
        from paude.dry_run import show_dry_run

        parsed_args = _parse_claude_args(claude_args)
        expanded = _expand_allowed_domains(allowed_domains)
        flags = {
            "yolo": yolo,
            "allowed_domains": expanded,
            "rebuild": rebuild,
            "backend": backend.value,
            "openshift_context": openshift_context,
            "openshift_namespace": openshift_namespace,
            "verbose": verbose,
            "claude_args": parsed_args,
        }
        show_dry_run(flags)
        raise typer.Exit()

    from paude.config import detect_config, parse_config

    workspace = Path.cwd()

    # Detect and parse config
    config_file = detect_config(workspace)
    config = None
    if config_file:
        try:
            config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Error parsing config: {e}", err=True)
            raise typer.Exit(1) from None

    # Shared pre-create: parse args, build env, expand domains, show warnings
    expanded_domains, parsed_args, env, unrestricted = _prepare_session_create(
        allowed_domains=allowed_domains,
        yolo=yolo,
        claude_args=claude_args,
        config_obj=config,
    )

    if backend == BackendType.podman:
        _create_podman_session(
            name=name,
            workspace=workspace,
            config=config,
            env=env,
            expanded_domains=expanded_domains,
            unrestricted=unrestricted,
            parsed_args=parsed_args,
            yolo=yolo,
            git=git,
            rebuild=rebuild,
            platform=platform,
        )
    else:
        _create_openshift_session(
            name=name,
            workspace=workspace,
            config=config,
            env=env,
            expanded_domains=expanded_domains,
            unrestricted=unrestricted,
            parsed_args=parsed_args,
            yolo=yolo,
            git=git,
            rebuild=rebuild,
            pvc_size=pvc_size,
            storage_class=storage_class,
            openshift_context=openshift_context,
            openshift_namespace=openshift_namespace,
            credential_timeout=credential_timeout,
        )


def _create_podman_session(
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
    rebuild: bool,
    platform: str | None,
) -> None:
    """Podman-specific session creation logic."""
    from paude.container import ImageManager
    from paude.mounts import build_mounts

    home = Path.home()
    image_manager = ImageManager(script_dir=_detect_dev_script_dir(), platform=platform)

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
    mounts = build_mounts(home)

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
    )

    try:
        backend_instance = PodmanBackend()
        session = backend_instance.create_session(session_config)

        # Auto-start the container (entrypoint is sleep infinity)
        backend_instance.start_session_no_attach(session.name)
    except SessionExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except Exception as e:
        typer.echo(f"Error creating session: {e}", err=True)
        try:
            backend_instance.delete_session(session.name, confirm=True)
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        raise typer.Exit(1) from None

    _finalize_session_create(
        session=session,
        expanded_domains=expanded_domains,
        yolo=yolo,
        git=git,
    )


def _create_openshift_session(
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
    rebuild: bool,
    pvc_size: str,
    storage_class: str | None,
    openshift_context: str | None,
    openshift_namespace: str | None,
    credential_timeout: int,
) -> None:
    """OpenShift-specific session creation logic."""
    from paude.backends.openshift import _generate_session_name

    os_script_dir = _detect_dev_script_dir()

    openshift_config = OpenShiftConfig(
        context=openshift_context,
        namespace=openshift_namespace,
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
        try:
            os_backend.delete_session(session_name, confirm=True)
        except Exception:  # noqa: S110 - best-effort cleanup
            pass
        raise typer.Exit(1) from None

    _finalize_session_create(
        session=session,
        expanded_domains=expanded_domains,
        yolo=yolo,
        git=git,
        openshift_context=openshift_context,
        openshift_namespace=os_backend.namespace,
    )
