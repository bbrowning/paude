"""The steps shared by every path that builds a session's container.

``paude create`` and ``paude upgrade`` are the same pipeline -- resolve a
composition, build the images, assemble the mounts, hand a ``SessionConfig`` to
the backend -- differing in one inserted step (upgrade tears the old container
down first) and in how they report failure. This module owns the steps; the two
callers own the order and the error UX, which is why these are three named
functions rather than one ``rebuild_session()`` with hooks.

``ImageManager``, ``build_mounts`` and the ``config_sync`` helpers are imported
inside the functions that use them, from the modules that define them, and must
stay that way for now: the create and upgrade suites patch them at those
*definition* paths, so a module-level import here would bind the name at import
time and silently defeat the patch. That is a property of the tests, not a
design goal -- patching the lookup site (``paude.cli.session_rebuild.X``), or
injecting the image manager the way ``engine`` already is, would free these.
Everything else is imported normally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import typer

from paude.backends.base import SessionConfig

if TYPE_CHECKING:
    from paude.agents.base import AgentComposition
    from paude.backends.labels import SessionSpec
    from paude.config.models import PaudeConfig
    from paude.container.engine import ContainerEngine
    from paude.transport.config_sync import RemoteConfigPaths


class ImageBuildError(RuntimeError):
    """An image build failed, carrying which one and why.

    The two callers report this very differently -- create exits, upgrade
    raises a retryable error -- so the stage is data rather than a message
    baked in here.
    """

    def __init__(self, stage: Literal["agent", "proxy"], cause: Exception) -> None:
        super().__init__(f"building the {stage} image failed: {cause}")
        self.stage = stage
        self.cause = cause


@dataclass(frozen=True)
class SessionImages:
    """The images a session runs: its agent container and its proxy sidecar."""

    agent: str
    proxy: str


@dataclass(frozen=True)
class SessionMounts:
    """Mount arguments, plus where their sources were synced for a remote."""

    mounts: list[str]
    remote_config: RemoteConfigPaths | None


def build_session_images(
    *,
    engine: ContainerEngine,
    composition: AgentComposition,
    config: PaudeConfig | None,
    workspace: Path,
    force_rebuild: bool,
    platform: str | None = None,
) -> SessionImages:
    """Build (or reuse) the agent and proxy images for a session.

    Both are built before any caller tears anything down, so a build failure --
    the most likely and most transient failure in a rebuild -- is always
    non-destructive.

    Raises:
        ImageBuildError: If either build fails, naming which one.
    """
    from paude.cli.helpers import _detect_dev_script_dir
    from paude.container import ImageManager

    image_manager = ImageManager(
        script_dir=_detect_dev_script_dir(),
        platform=platform,
        agent=composition.primary,
        composition=composition,
        engine=engine,
    )

    try:
        if config is not None and config.has_customizations:
            agent_image = image_manager.ensure_custom_image(
                config, force_rebuild=force_rebuild, workspace=workspace
            )
        else:
            agent_image = image_manager.ensure_default_image(
                force_rebuild=force_rebuild
            )
    except Exception as e:
        raise ImageBuildError("agent", e) from e

    # Always required: every session gets a proxy.
    try:
        proxy_image = image_manager.ensure_proxy_image(force_rebuild=force_rebuild)
    except Exception as e:
        raise ImageBuildError("proxy", e) from e

    return SessionImages(agent=agent_image, proxy=proxy_image)


def prepare_session_mounts(
    *,
    engine: ContainerEngine,
    composition: AgentComposition,
) -> SessionMounts:
    """Assemble the session's mounts, syncing config to a remote host if needed.

    Local engines skip the config bind mounts and copy the files in afterwards
    instead, which avoids SELinux relabelling. SSH remotes keep the bind mounts,
    but their sources are local paths that do not exist on the remote host, so
    the files are transferred and the mount sources rewritten -- otherwise
    podman there fails with "statfs ...: no such file".
    """
    from paude.mounts import build_mounts

    mounts = build_mounts(Path.home(), composition, include_config=engine.is_remote)
    if not engine.is_remote:
        return SessionMounts(mounts=mounts, remote_config=None)

    from paude.transport.config_sync import remap_mounts, sync_configs_to_remote
    from paude.transport.ssh import SshTransport

    if not isinstance(engine.transport, SshTransport):
        return SessionMounts(mounts=mounts, remote_config=None)

    typer.echo("Syncing configuration to remote host...", err=True)
    remote_config = sync_configs_to_remote(engine.transport, mounts)
    return SessionMounts(
        mounts=remap_mounts(mounts, remote_config.path_map),
        remote_config=remote_config,
    )


def session_config_from_spec(
    spec: SessionSpec,
    *,
    name: str | None,
    workspace: Path,
    composition: AgentComposition,
    images: SessionImages,
    env: dict[str, str],
    mounts: list[str],
    expanded_domains: list[str],
    otel_ports: list[int],
    args: list[str] | None = None,
    workdir: str | None = None,
    reuse_volume: bool = False,
) -> SessionConfig:
    """Turn a resolved spec plus this build's outputs into a SessionConfig.

    Reads the spec verbatim: both callers normalise theirs first, so no
    caller-specific default lives here.
    """
    return SessionConfig(
        name=name,
        workspace=workspace,
        image=images.agent,
        env=env,
        mounts=mounts,
        args=args or [],
        workdir=workdir,
        # The expanded set, deliberately not spec.allowed_domains: the spec
        # records what was declared (or None for a session predating the
        # always-on proxy), the container needs what that expands to.
        allowed_domains=expanded_domains,
        yolo=spec.yolo,
        proxy_image=images.proxy,
        agent=spec.agent,
        provider=spec.provider,
        agent_providers=list(spec.agent_providers),
        credential_providers=list(spec.credential_providers),
        gpu=spec.gpu,
        reuse_volume=reuse_volume,
        ports=composition.exposed_ports,
        otel_ports=otel_ports,
        otel_endpoint=spec.otel_endpoint,
    )
