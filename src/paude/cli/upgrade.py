"""Upgrade command for paude sessions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from paude.backends import SessionNotFoundError
from paude.cli.app import BackendType, app
from paude.cli.helpers import find_session_backend

if TYPE_CHECKING:
    from paude.backends.openshift import OpenShiftBackend
    from paude.backends.podman.backend import PodmanBackend


@dataclass
class UpgradeOverrides:
    """CLI overrides for session configuration during upgrade."""

    otel_endpoint: str | None = None
    allowed_domains: list[str] | None = None
    gpu: str | None = None  # "" means explicitly disabled
    yolo: bool | None = None
    provider: str | None = None

    def has_changes(self) -> bool:
        """Return True if any override was specified."""
        return (
            self.otel_endpoint is not None
            or self.allowed_domains is not None
            or self.gpu is not None
            or self.yolo is not None
            or self.provider is not None
        )


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
    otel_endpoint: Annotated[
        str | None,
        typer.Option(
            "--otel-endpoint",
            help=(
                "Set or change the OTLP collector endpoint "
                "(e.g., http://collector:4318). "
                'Use --otel-endpoint "" to remove.'
            ),
        ),
    ] = None,
    allowed_domains: Annotated[
        list[str] | None,
        typer.Option(
            "--allowed-domains",
            help="Override allowed domains for network filtering.",
        ),
    ] = None,
    gpu: Annotated[
        str | None,
        typer.Option(
            "--gpu",
            help="Set or change GPU passthrough (e.g., all, device=0,1).",
        ),
    ] = None,
    no_gpu: Annotated[
        bool,
        typer.Option(
            "--no-gpu",
            help="Disable GPU passthrough.",
        ),
    ] = False,
    yolo: Annotated[
        bool,
        typer.Option("--yolo", help="Enable YOLO mode."),
    ] = False,
    no_yolo: Annotated[
        bool,
        typer.Option("--no-yolo", help="Disable YOLO mode."),
    ] = False,
    provider: Annotated[
        str | None,
        typer.Option(
            "--provider",
            help="Change inference provider (e.g., vertex, openai).",
        ),
    ] = None,
) -> None:
    """Upgrade a session to the current paude version.

    Can also reconfigure session options (e.g., --otel-endpoint, --gpu)
    without losing workspace data. Use --rebuild to force an image rebuild
    when only changing configuration at the same version.
    """
    from paude import __version__
    from paude.backends.openshift import OpenShiftBackend
    from paude.backends.podman.backend import PodmanBackend
    from paude.cli.helpers import _get_backend_instance

    # Resolve --gpu / --no-gpu
    cli_gpu: str | None = gpu
    if no_gpu:
        cli_gpu = ""  # empty string sentinel = explicitly disabled

    # Resolve --yolo / --no-yolo
    cli_yolo: bool | None = None
    if yolo:
        cli_yolo = True
    elif no_yolo:
        cli_yolo = False

    # Build overrides dict (only non-None values)
    overrides = UpgradeOverrides(
        otel_endpoint=otel_endpoint,
        allowed_domains=allowed_domains,
        gpu=cli_gpu,
        yolo=cli_yolo,
        provider=provider,
    )

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

    has_overrides = overrides.has_changes()

    # Check version
    if session.version == __version__ and not rebuild and not has_overrides:
        typer.echo(
            f"Session '{name}' is already at version {__version__}. "
            "Use --rebuild to force an image rebuild, or pass config "
            "flags (e.g. --otel-endpoint) to reconfigure."
        )
        return

    if has_overrides and not rebuild and session.version == __version__:
        typer.echo(
            f"Reconfiguring session '{name}' (version {__version__})...",
            err=True,
        )
    else:
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
            _upgrade_podman(name, backend_obj, rebuild, overrides)
        elif isinstance(backend_obj, OpenShiftBackend):
            _upgrade_openshift(name, backend_obj, rebuild, openshift_context, overrides)
        else:
            typer.echo("Unsupported backend for upgrade.", err=True)
            raise typer.Exit(1)
    except SessionNotFoundError as e:
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
    overrides: UpgradeOverrides,
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
        PAUDE_LABEL_OTEL_ENDPOINT,
        PAUDE_LABEL_PROVIDER,
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
    provider_name = labels.get(PAUDE_LABEL_PROVIDER)
    workspace_encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    workspace = (
        decode_path(workspace_encoded, url_safe=True)
        if workspace_encoded
        else Path.cwd()
    )
    gpu = labels.get(PAUDE_LABEL_GPU)
    yolo = labels.get(PAUDE_LABEL_YOLO) == "1"
    otel_endpoint = labels.get(PAUDE_LABEL_OTEL_ENDPOINT)

    # Domain config
    domains_str = labels.get(PAUDE_LABEL_DOMAINS)
    allowed_domains: list[str] | None = None
    if domains_str is not None:
        allowed_domains = domains_str.split(",") if domains_str else []

    proxy_image_label = labels.get(PAUDE_LABEL_PROXY_IMAGE)

    # Apply CLI overrides
    if overrides.provider is not None:
        provider_name = overrides.provider
    if overrides.gpu is not None:
        gpu = overrides.gpu if overrides.gpu != "" else None
    if overrides.yolo is not None:
        yolo = overrides.yolo
    if overrides.otel_endpoint is not None:
        # Empty string means "remove OTEL"
        otel_endpoint = overrides.otel_endpoint if overrides.otel_endpoint else None
    if overrides.allowed_domains is not None:
        allowed_domains = overrides.allowed_domains

    # Detect project config from workspace
    config = None
    config_file = detect_config(workspace)
    if config_file:
        config = parse_config(config_file)

    # Build new image
    engine = backend._engine
    agent_instance = get_agent(agent_name, provider=provider_name)
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
            image = image_manager.ensure_default_image(force_rebuild=rebuild)
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

    # Only expand domains if the session has domain filtering.
    # allowed_domains=None means no proxy (unrestricted);
    # passing None to _prepare_session_create would incorrectly add defaults.
    if allowed_domains is not None:
        expanded_domains, parsed_args, _env, unrestricted = _prepare_session_create(
            allowed_domains,
            yolo,
            None,
            config,
            agent_name=agent_name,
            otel_endpoint=otel_endpoint,
        )
        session_domains = expanded_domains if not unrestricted else allowed_domains
    else:
        session_domains = None
        # Even without proxy, inject OTEL env vars
        if otel_endpoint:
            from paude.otel import build_otel_env

            env.update(build_otel_env(agent_name, otel_endpoint))

    # Compute OTEL proxy ports
    otel_ports: list[int] = []
    if otel_endpoint:
        from paude.otel import otel_proxy_ports

        otel_ports = otel_proxy_ports(otel_endpoint)

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
        provider=provider_name,
        gpu=gpu,
        reuse_volume=True,
        ports=agent_instance.config.exposed_ports,
        otel_ports=otel_ports,
        otel_endpoint=otel_endpoint,
    )

    backend.create_session(session_config)
    github_token = os.environ.get("PAUDE_GITHUB_TOKEN")
    backend.start_session_no_attach(name, github_token=github_token)


def _upgrade_openshift(
    name: str,
    backend: OpenShiftBackend,
    rebuild: bool,
    openshift_context: str | None,
    overrides: UpgradeOverrides,
) -> None:
    """Upgrade an OpenShift session in place."""
    from paude import __version__
    from paude.agents import get_agent
    from paude.backends.shared import (
        PAUDE_LABEL_AGENT,
        PAUDE_LABEL_GPU,
        PAUDE_LABEL_OTEL_ENDPOINT,
        PAUDE_LABEL_PROVIDER,
        PAUDE_LABEL_VERSION,
        PAUDE_LABEL_YOLO,
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
    provider_name = labels.get(PAUDE_LABEL_PROVIDER)
    workspace_encoded = annotations.get("paude.io/workspace", "")
    workspace = decode_path(workspace_encoded) if workspace_encoded else Path.cwd()
    otel_endpoint = annotations.get(PAUDE_LABEL_OTEL_ENDPOINT)
    old_otel_endpoint = otel_endpoint

    # Apply CLI overrides
    if overrides.provider is not None:
        provider_name = overrides.provider
    if overrides.otel_endpoint is not None:
        otel_endpoint = overrides.otel_endpoint if overrides.otel_endpoint else None

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
        agent=get_agent(agent_name, provider=provider_name),
    )

    # Patch StatefulSet with new image
    sts_name = resource_name(name)
    ns = backend.namespace
    oc = backend._lifecycle._oc

    import json as _json

    # Build JSON patches -- always update image and version
    patches: list[dict[str, str]] = [
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/image",
            "value": image,
        },
    ]

    # If overrides change env vars, we need to patch the container env.
    # Build a map of env var overrides to apply.
    env_overrides: dict[str, str | None] = {}
    if overrides.otel_endpoint is not None:
        if otel_endpoint:
            from paude.otel import build_otel_env

            env_overrides.update(build_otel_env(agent_name, otel_endpoint))
        else:
            # Clearing OTEL -- remove known OTEL env vars
            from paude.otel import OTEL_ENV_KEYS

            for key in OTEL_ENV_KEYS:
                env_overrides[key] = None  # sentinel for removal

    if env_overrides:
        # Get current env list from StatefulSet spec
        containers = (
            sts.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        current_env = containers[0].get("env", []) if containers else []

        # Apply overrides
        new_env = [e for e in current_env if e["name"] not in env_overrides]
        for key, value in env_overrides.items():
            if value is not None:
                new_env.append({"name": key, "value": value})

        patches.append(
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/env",
                "value": new_env,  # type: ignore[dict-item]
            }
        )

    typer.echo(f"Patching StatefulSet {sts_name}...", err=True)
    patch_json = _json.dumps(patches)
    oc.run(
        "patch",
        "statefulset",
        sts_name,
        "-n",
        ns,
        "--type=json",
        "-p",
        patch_json,
    )

    # Update labels and annotations
    label_updates = [f"{PAUDE_LABEL_VERSION}={__version__}"]
    if overrides.provider is not None:
        label_updates.append(f"{PAUDE_LABEL_PROVIDER}={provider_name or ''}")
    if overrides.gpu is not None:
        label_updates.append(f"{PAUDE_LABEL_GPU}={overrides.gpu}")
    if overrides.yolo is not None:
        label_updates.append(f"{PAUDE_LABEL_YOLO}={'1' if overrides.yolo else '0'}")

    oc.run(
        "label",
        "statefulset",
        sts_name,
        "-n",
        ns,
        *label_updates,
        "--overwrite",
    )

    # Update otel-endpoint annotation
    if overrides.otel_endpoint is not None:
        ann_value = otel_endpoint or ""
        oc.run(
            "annotate",
            "statefulset",
            sts_name,
            "-n",
            ns,
            f"{PAUDE_LABEL_OTEL_ENDPOINT}={ann_value}",
            "--overwrite",
        )

    # Scale to 1 and wait
    typer.echo(f"Starting session '{name}'...", err=True)
    backend._lifecycle._scale_statefulset(name, 1)

    if backend._lookup.has_proxy_deployment(name):
        from paude.backends.shared import proxy_resource_name

        proxy_image: str | None = None
        if script_dir is not None:
            typer.echo("Building proxy image in OpenShift cluster...", err=True)
            proxy_image = backend.ensure_proxy_image_via_build(
                script_dir, force_rebuild=rebuild, session_name=name
            )
        else:
            from paude.backends.openshift.session_lifecycle import (
                resolve_proxy_image,
            )

            proxy_image = resolve_proxy_image(image)

        # Update proxy allowed domains when OTEL endpoint changes
        if overrides.otel_endpoint is not None:
            from paude.otel import otel_proxy_ports, parse_otel_endpoint

            current_domains = backend._proxy.get_deployment_domains(name)
            otel_ports: list[int] = []

            if otel_endpoint:
                hostname, _ = parse_otel_endpoint(otel_endpoint)
                if hostname not in current_domains:
                    current_domains.append(hostname)
                otel_ports = otel_proxy_ports(otel_endpoint)
            else:
                if old_otel_endpoint:
                    old_hostname, _ = parse_otel_endpoint(old_otel_endpoint)
                    current_domains = [d for d in current_domains if d != old_hostname]

            from paude.agents.base import build_secret_environment_from_config as _bsec

            _upgrade_agent = get_agent(agent_name, provider=provider_name)
            _proxy_creds = _bsec(_upgrade_agent.config)
            _gh = os.environ.get("PAUDE_GITHUB_TOKEN")
            if _gh:
                _proxy_creds["GH_TOKEN"] = _gh
            backend._proxy.update_deployment_domains(
                name,
                current_domains,
                otel_ports=otel_ports,
                image=proxy_image,
                credentials=_proxy_creds,
            )
        elif proxy_image is not None:
            backend._proxy.update_deployment_image(name, proxy_image)

        backend._lifecycle._scale_deployment(proxy_resource_name(name), 1)
        backend._proxy.wait_for_ready(name)

    pname = pod_name(name)
    typer.echo(f"Waiting for pod {pname} to be ready...", err=True)
    backend._pod_waiter.wait_for_ready(pname)

    # Re-sync config
    from paude.agents.base import build_secret_environment_from_config
    from paude.backends.shared import PROXY_MANAGED_CREDENTIAL

    agent_instance = get_agent(agent_name, provider=provider_name)
    _proxy_active = backend._lookup.has_proxy_deployment(name)
    if _proxy_active:
        secret_env = dict.fromkeys(
            agent_instance.config.secret_env_vars, PROXY_MANAGED_CREDENTIAL
        )
    else:
        secret_env = build_secret_environment_from_config(agent_instance.config)
    backend._syncer.sync_full_config(
        pname,
        agent_name=agent_name,
        provider=provider_name,
        secret_env=secret_env,
        proxy_active=_proxy_active,
    )

    github_token = os.environ.get("PAUDE_GITHUB_TOKEN")
    backend.start_agent_headless(name, github_token=github_token)
