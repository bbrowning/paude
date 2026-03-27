"""Upgrade command for paude sessions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.backends.openshift import (
    SessionNotFoundError as OpenshiftSessionNotFoundError,
)
from paude.cli.app import BackendType, app
from paude.cli.helpers import find_session_backend

if TYPE_CHECKING:
    from paude.backends.openshift import OpenShiftBackend
    from paude.backends.podman.backend import PodmanBackend


@app.command("upgrade")
def session_upgrade(
    name: Annotated[str, typer.Argument(help="Session name to upgrade")],
    rebuild: Annotated[
        bool,
        typer.Option("--rebuild", help="Force image rebuild even at same version."),
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
        typer.Option("--openshift-context", help="Kubeconfig context for OpenShift."),
    ] = None,
    openshift_namespace: Annotated[
        str | None,
        typer.Option(
            "--openshift-namespace",
            help="OpenShift namespace (default: current context namespace).",
        ),
    ] = None,
) -> None:
    """Upgrade a session to the current paude version."""
    from paude import __version__
    from paude.backends.openshift import OpenShiftBackend
    from paude.backends.podman.backend import PodmanBackend
    from paude.cli.helpers import _get_backend_instance

    # Find session backend
    if backend is not None:
        backend_obj = _get_backend_instance(
            backend, openshift_context, openshift_namespace
        )
    else:
        result = find_session_backend(name, openshift_context, openshift_namespace)
        if result is None:
            typer.echo(f"Session '{name}' not found.", err=True)
            raise typer.Exit(1)
        backend, backend_obj = result

    # Get session
    session = backend_obj.get_session(name)
    if session is None:
        typer.echo(f"Session '{name}' not found.", err=True)
        raise typer.Exit(1)

    # Check version
    if session.version == __version__ and not rebuild:
        typer.echo(
            f"Session '{name}' is already at version {__version__}. "
            "Use --rebuild to force an image rebuild."
        )
        return

    old_version = session.version or "unknown"
    typer.echo(
        f"Upgrading session '{name}' from {old_version} to {__version__}...",
        err=True,
    )

    # Auto-stop if running
    if session.status == "running":
        typer.echo(f"Stopping session '{name}'...", err=True)
        backend_obj.stop_session(name)

    try:
        if isinstance(backend_obj, PodmanBackend):
            _upgrade_podman(name, backend_obj, rebuild)
        elif isinstance(backend_obj, OpenShiftBackend):
            _upgrade_openshift(name, backend_obj, rebuild, openshift_context)
        else:
            typer.echo("Unsupported backend for upgrade.", err=True)
            raise typer.Exit(1)
    except (SessionNotFoundError, OpenshiftSessionNotFoundError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error upgrading session: {e}", err=True)
        raise typer.Exit(1) from None

    # Update registry
    from paude.registry import SessionRegistry

    registry = SessionRegistry()
    entries = registry.load()
    if name in entries:
        entries[name].paude_version = __version__
        registry._save(entries)

    typer.echo(f"Session '{name}' upgraded to version {__version__}.")


def _upgrade_podman(
    name: str,
    backend: PodmanBackend,
    rebuild: bool,
) -> None:
    """Upgrade a Podman/Docker session in place."""
    from paude.agents import get_agent
    from paude.backends.podman.helpers import (
        container_name,
        find_container_by_session_name,
        network_name,
        proxy_container_name,
    )
    from paude.backends.shared import (
        PAUDE_LABEL_AGENT,
        PAUDE_LABEL_DOMAINS,
        PAUDE_LABEL_GPU,
        PAUDE_LABEL_PROXY_IMAGE,
        PAUDE_LABEL_WORKSPACE,
        PAUDE_LABEL_YOLO,
        decode_path,
    )
    from paude.cli.helpers import (
        _detect_dev_script_dir,
        _prepare_session_create,
    )
    from paude.config.detector import detect_config
    from paude.config.parser import parse_config
    from paude.container import ImageManager
    from paude.mounts import build_mounts

    # Get container and extract labels
    container = find_container_by_session_name(backend._runner, name)
    if container is None:
        typer.echo(f"Container for session '{name}' not found.", err=True)
        raise typer.Exit(1)

    labels = container.get("Labels", {}) or {}

    agent_name = labels.get(PAUDE_LABEL_AGENT, "claude")
    workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    workspace = (
        decode_path(workspace_encoded, url_safe=True)
        if workspace_encoded
        else Path.cwd()
    )
    gpu = labels.get(PAUDE_LABEL_GPU)
    yolo = labels.get(PAUDE_LABEL_YOLO) == "1"

    # Domain config
    domains_str = labels.get(PAUDE_LABEL_DOMAINS)
    allowed_domains: list[str] | None = None
    if domains_str is not None:
        allowed_domains = domains_str.split(",") if domains_str else []

    proxy_image_label = labels.get(PAUDE_LABEL_PROXY_IMAGE)

    # Detect project config from workspace
    config = None
    config_file = detect_config(workspace)
    if config_file:
        config = parse_config(config_file)

    # Build new image
    engine = backend._engine
    agent_instance = get_agent(agent_name)
    image_manager = ImageManager(
        script_dir=_detect_dev_script_dir(),
        agent=agent_instance,
        engine=engine,
    )

    try:
        if config is not None and config.has_customizations:
            image = image_manager.ensure_custom_image(
                config, force_rebuild=rebuild, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image()
    except Exception as e:
        typer.echo(f"Error building image: {e}", err=True)
        raise typer.Exit(1) from None

    # Build proxy image if needed
    proxy_image: str | None = None
    if allowed_domains is not None:
        try:
            proxy_image = image_manager.ensure_proxy_image(force_rebuild=rebuild)
        except Exception as e:
            typer.echo(f"Error building proxy image: {e}", err=True)
            raise typer.Exit(1) from None

    # Remove old container and proxy resources (but NOT the volume)
    cname = container_name(name)
    typer.echo(f"Removing old container {cname}...", err=True)
    backend._runner.remove_container(cname, force=True)

    pname = proxy_container_name(name)
    backend._runner.remove_container(pname, force=True)
    nname = network_name(name)
    backend._network_manager.remove_network(nname)

    # Build mounts and env
    home = Path.home()
    mounts = build_mounts(home, agent_instance, include_config=engine.is_remote)

    # Build environment (passthrough vars like CLAUDE_CODE_USE_VERTEX)
    env = agent_instance.build_environment()
    if config and config.container_env:
        env.update(config.container_env)

    # Only expand domains if the original session had domain filtering.
    # allowed_domains=None means the session had no proxy (unrestricted);
    # passing None to _prepare_session_create would incorrectly add defaults.
    if allowed_domains is not None:
        expanded_domains, parsed_args, _env, unrestricted = _prepare_session_create(
            allowed_domains, yolo, None, config, agent_name=agent_name
        )
        session_domains = expanded_domains if not unrestricted else allowed_domains
    else:
        session_domains = None

    # Create new session config with reuse_volume=True
    from paude.backends import SessionConfig

    session_config = SessionConfig(
        name=name,
        workspace=workspace,
        image=image,
        env=env,
        mounts=mounts,
        allowed_domains=session_domains,
        yolo=yolo,
        proxy_image=proxy_image or proxy_image_label,
        agent=agent_name,
        gpu=gpu,
        reuse_volume=True,
    )

    backend.create_session(session_config)
    backend.start_session_no_attach(name)


def _upgrade_openshift(
    name: str,
    backend: OpenShiftBackend,
    rebuild: bool,
    openshift_context: str | None,
) -> None:
    """Upgrade an OpenShift session in place."""
    from paude import __version__
    from paude.agents import get_agent
    from paude.backends.shared import (
        PAUDE_LABEL_AGENT,
        PAUDE_LABEL_VERSION,
        decode_path,
        pod_name,
        resource_name,
    )
    from paude.cli.helpers import _detect_dev_script_dir
    from paude.config.detector import detect_config
    from paude.config.parser import parse_config

    # Get StatefulSet
    sts = backend._lookup.get_statefulset(name)
    if sts is None:
        typer.echo(f"StatefulSet for session '{name}' not found.", err=True)
        raise typer.Exit(1)

    metadata = sts.get("metadata", {})
    labels = metadata.get("labels", {})
    annotations = metadata.get("annotations", {})

    agent_name = labels.get(PAUDE_LABEL_AGENT, "claude")
    workspace_encoded = annotations.get("paude.io/workspace", "")
    workspace = decode_path(workspace_encoded) if workspace_encoded else Path.cwd()

    # Detect project config from workspace
    config = None
    config_file = detect_config(workspace)
    if config_file:
        config = parse_config(config_file)

    # Build new image
    script_dir = _detect_dev_script_dir()
    typer.echo("Building image in OpenShift cluster...", err=True)
    image = backend.ensure_image_via_build(
        config=config,
        workspace=workspace,
        script_dir=script_dir,
        force_rebuild=rebuild,
        session_name=name,
        agent=get_agent(agent_name),
    )

    # Patch StatefulSet with new image
    sts_name = resource_name(name)
    ns = backend.namespace
    oc = backend._lifecycle._oc

    import json as _json

    typer.echo(f"Patching StatefulSet {sts_name} with new image...", err=True)
    patch = _json.dumps(
        [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/image",
                "value": image,
            }
        ]
    )
    oc.run(
        "patch",
        "statefulset",
        sts_name,
        "-n",
        ns,
        "--type=json",
        "-p",
        patch,
    )

    # Update version label
    oc.run(
        "label",
        "statefulset",
        sts_name,
        "-n",
        ns,
        f"{PAUDE_LABEL_VERSION}={__version__}",
        "--overwrite",
    )

    # Scale to 1 and wait
    typer.echo(f"Starting session '{name}'...", err=True)
    backend._lifecycle._scale_statefulset(name, 1)

    if backend._lookup.has_proxy_deployment(name):
        from paude.backends.shared import proxy_resource_name

        backend._lifecycle._scale_deployment(proxy_resource_name(name), 1)
        backend._proxy.wait_for_ready(name)

    pname = pod_name(name)
    typer.echo(f"Waiting for pod {pname} to be ready...", err=True)
    backend._pod_waiter.wait_for_ready(pname)

    # Re-sync config
    from paude.agents.base import build_secret_environment_from_config

    agent_instance = get_agent(agent_name)
    secret_env = build_secret_environment_from_config(agent_instance.config)
    backend._syncer.sync_full_config(
        pname, agent_name=agent_name, secret_env=secret_env
    )
