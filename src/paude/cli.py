"""Typer CLI for paude."""

from __future__ import annotations

import os
from typing import Annotated

import typer

from paude import __version__

app = typer.Typer(
    name="paude",
    help="Run Claude Code in an isolated Podman container.",
    add_completion=False,
    context_settings={"allow_interspersed_args": False},
)


def version_callback(value: bool) -> None:
    """Print version information and exit."""
    if value:
        typer.echo(f"paude {__version__}")
        dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        registry = os.environ.get("PAUDE_REGISTRY", "docker.io/bbrowning")
        if dev_mode:
            typer.echo("  mode: development (PAUDE_DEV=1, building locally)")
        else:
            typer.echo(f"  mode: installed (pulling from {registry})")
        raise typer.Exit()


def show_help() -> None:
    """Show custom help message matching bash format."""
    help_text = """paude - Run Claude Code in a secure Podman container

USAGE:
    paude [OPTIONS] [-- CLAUDE_ARGS...]

OPTIONS:
    -h, --help          Show this help message and exit
    -V, --version       Show paude version and exit
    --yolo              Enable YOLO mode (skip all permission prompts)
                        Claude can edit files and run commands without confirmation
    --allow-network     Allow unrestricted network access
                        By default, network is restricted to Vertex AI endpoints only
    --rebuild           Force rebuild of workspace container image
                        Use when devcontainer.json has changed
    --dry-run           Show configuration and what would be done, then exit
                        Useful for verifying paude.json or devcontainer.json

CLAUDE OPTIONS:
    All arguments after -- are passed directly to claude.
    Run 'paude -- --help' to see claude's options.

EXAMPLES:
    paude                           Start interactive claude session
    paude --yolo                    Start with YOLO mode (no permission prompts)
    paude -- -p "What is 2+2?"      Run claude with a prompt
    paude --yolo -- -p "Fix bugs"   YOLO mode with a prompt
    paude -- --help                 Show claude's help

SECURITY:
    By default, paude runs with network restricted to Google/Anthropic APIs only.
    Use --allow-network to permit all network access (enables data exfiltration).
    Combining --yolo with --allow-network is maximum risk mode."""
    typer.echo(help_text)


def help_callback(value: bool) -> None:
    """Print help and exit."""
    if value:
        show_help()
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Show paude version and exit.",
        ),
    ] = False,
    help_opt: Annotated[
        bool,
        typer.Option(
            "--help",
            "-h",
            callback=help_callback,
            is_eager=True,
            help="Show this help message and exit.",
        ),
    ] = False,
    yolo: Annotated[
        bool,
        typer.Option(
            "--yolo",
            help="Enable YOLO mode (skip all permission prompts).",
        ),
    ] = False,
    allow_network: Annotated[
        bool,
        typer.Option(
            "--allow-network",
            help="Allow unrestricted network access.",
        ),
    ] = False,
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
        list[str] | None,
        typer.Argument(help="Arguments to pass to claude (after --)"),
    ] = None,
) -> None:
    """Run Claude Code in an isolated Podman container."""
    # Store flags for use by other modules
    ctx.ensure_object(dict)
    ctx.obj["yolo"] = yolo
    ctx.obj["allow_network"] = allow_network
    ctx.obj["rebuild"] = rebuild
    ctx.obj["dry_run"] = dry_run
    ctx.obj["claude_args"] = claude_args or []

    if dry_run:
        from paude.dry_run import show_dry_run

        show_dry_run(ctx.obj)
        raise typer.Exit()

    # Main execution flow
    import atexit
    import signal
    import sys
    from pathlib import Path

    from paude.config import detect_config, parse_config
    from paude.container import ContainerRunner, ImageManager, NetworkManager
    from paude.environment import build_environment, build_proxy_environment
    from paude.mounts import build_mounts, build_venv_mounts, get_venv_paths
    from paude.platform import check_macos_volumes, get_podman_machine_dns
    from paude.utils import check_git_safety, check_requirements

    # Get script directory for dev mode
    script_dir: Path | None = None
    dev_path = Path(__file__).parent.parent.parent
    if (dev_path / "containers" / "paude" / "Dockerfile").exists():
        script_dir = dev_path

    workspace = Path.cwd()
    home = Path.home()

    # Check requirements
    try:
        check_requirements()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from None

    # Detect and parse config
    config_file = detect_config(workspace)
    config = None
    if config_file:
        try:
            config = parse_config(config_file)
        except Exception as e:
            typer.echo(f"Error parsing config: {e}", err=True)
            raise typer.Exit(1) from None

    # Create managers
    image_manager = ImageManager(script_dir=script_dir)
    network_manager = NetworkManager()
    runner = ContainerRunner()

    # Track resources for cleanup
    proxy_container: str | None = None
    network_name: str | None = None

    def cleanup() -> None:
        """Clean up resources on exit."""
        if proxy_container:
            runner.stop_container(proxy_container)
        # Note: We don't remove the network - it's reused across invocations
        # This matches the bash implementation which keeps "paude-internal"

    atexit.register(cleanup)
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(130))
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(143))

    # Ensure images
    try:
        if config and (config.base_image or config.dockerfile or config.pip_install):
            image = image_manager.ensure_custom_image(
                config, force_rebuild=rebuild, workspace=workspace
            )
        else:
            image = image_manager.ensure_default_image()
    except Exception as e:
        typer.echo(f"Error ensuring image: {e}", err=True)
        raise typer.Exit(1) from None

    # Build mounts and environment
    mounts = build_mounts(workspace, home)

    # Add venv shadow mounts (must come after workspace mount)
    venv_mode = config.venv if config else "auto"
    venv_mounts = build_venv_mounts(workspace, venv_mode)
    mounts.extend(venv_mounts)

    env = build_environment()

    # Add container env from config
    if config and config.container_env:
        env.update(config.container_env)

    # Add PAUDE_VENV_PATHS when pip_install is enabled
    if config and config.pip_install:
        venv_paths = get_venv_paths(workspace, config.venv)
        if venv_paths:
            env["PAUDE_VENV_PATHS"] = ":".join(str(p) for p in venv_paths)

    # Check macOS volumes
    if not check_macos_volumes(workspace, image):
        raise typer.Exit(1)

    # Check git safety
    check_git_safety(workspace)

    # Setup proxy if not allow-network
    if not allow_network:
        try:
            # Create internal network (reused across invocations)
            network_name = "paude-internal"
            network_manager.create_internal_network(network_name)

            # Start proxy
            proxy_image = image_manager.ensure_proxy_image()
            dns = get_podman_machine_dns()
            proxy_container = runner.run_proxy(proxy_image, network_name, dns)

            # Add proxy env vars
            env.update(build_proxy_environment(proxy_container))
        except Exception as e:
            typer.echo(f"Error setting up proxy: {e}", err=True)
            cleanup()
            raise typer.Exit(1) from None

    # Run postCreateCommand if present and this is first run
    # Uses .paude-initialized marker to track if command has run
    workspace_marker = workspace / ".paude-initialized"
    if config and config.post_create_command and not workspace_marker.exists():
        typer.echo(f"Running postCreateCommand: {config.post_create_command}")
        success = runner.run_post_create(
            image=image,
            mounts=mounts,
            env=env,
            command=config.post_create_command,
            workdir=str(workspace),
            network=network_name,
        )
        if not success:
            typer.echo("Warning: postCreateCommand failed", err=True)
        else:
            # Mark as initialized only on success (matches bash behavior)
            try:
                workspace_marker.touch()
            except OSError:
                pass  # Ignore if we can't create marker

    # Run Claude
    try:
        exit_code = runner.run_claude(
            image=image,
            mounts=mounts,
            env=env,
            args=claude_args or [],
            workdir=str(workspace),
            network=network_name,
            yolo=yolo,
            allow_network=allow_network,
        )
    except Exception as e:
        typer.echo(f"Error running Claude: {e}", err=True)
        cleanup()
        raise typer.Exit(1) from None

    cleanup()
    raise typer.Exit(exit_code)
