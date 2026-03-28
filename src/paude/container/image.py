"""Image management for paude containers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from paude import __version__
from paude.agents.base import Agent
from paude.config.claude_layer import generate_claude_layer_dockerfile
from paude.config.models import PaudeConfig
from paude.container.build_context import (
    BuildContext,
    copy_entrypoints,
    copy_features_cache,
    generate_dockerfile_content,
    prepare_build_context,
    resolve_entrypoint,
)
from paude.container.engine import ContainerEngine
from paude.hash import compute_config_hash, compute_content_hash
from paude.platform import is_macos

# Re-export for backward compatibility
__all__ = ["BuildContext", "ImageManager", "prepare_build_context"]


def _detect_native_platform() -> str:
    """Detect the native platform for container builds."""
    import platform as plat

    machine = plat.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "linux/arm64"
    return "linux/amd64"


class ImageManager:
    """Manages container images for paude."""

    def __init__(
        self,
        script_dir: Path | None = None,
        platform: str | None = None,
        agent: Agent | None = None,
        engine: ContainerEngine | None = None,
    ):
        self.script_dir = script_dir
        self.dev_mode = os.environ.get("PAUDE_DEV", "0") == "1"
        self.registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
        self.version = __version__
        self.platform = platform if platform is not None else _detect_native_platform()
        if agent is None:
            from paude.agents import get_agent

            agent = get_agent("claude")
        self.agent = agent
        self._engine = engine or ContainerEngine()

    def ensure_default_image(self) -> str:
        """Ensure the default paude image is available.

        Returns:
            Image tag to use (the runtime image with agent installed).
        """
        base_tag = self._ensure_base_image()
        return self._ensure_runtime_image(base_tag)

    def _ensure_base_image(self) -> str:
        """Ensure the base paude image (without agent) is available."""
        import sys

        if self.dev_mode and self.script_dir:
            if self.platform:
                arch = self.platform.split("/")[-1]
                tag = f"paude-base-centos10:latest-{arch}"
            else:
                tag = "paude-base-centos10:latest"
            if not self._engine.image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "paude" / "Dockerfile"
                context = self.script_dir / "containers" / "paude"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            tag = f"{self.registry}/paude-base-centos10:{self.version}"
            if not self._engine.image_exists(tag):
                print(f"Pulling {tag}...", file=sys.stderr)
                try:
                    self._engine.run(
                        "pull", "--platform", self.platform, tag, capture=False
                    )
                except Exception:
                    engine_name = self._engine.binary
                    print(
                        f"Check your network connection or run '{engine_name} login' "
                        "if authentication is required.",
                        file=sys.stderr,
                    )
                    raise
            return tag

    def _ensure_runtime_image(self, base_image: str) -> str:
        """Ensure the runtime image (with agent installed) is available."""
        import sys

        layer_content = generate_claude_layer_dockerfile(agent=self.agent)
        layer_hash = compute_content_hash(
            base_image.encode(),
            self.version.encode(),
            layer_content.encode(),
        )

        if self.platform:
            arch = self.platform.split("/")[-1]
            runtime_tag = f"paude-runtime:{layer_hash[:12]}-{arch}"
        else:
            runtime_tag = f"paude-runtime:{layer_hash[:12]}"

        if self._engine.image_exists(runtime_tag):
            print(f"Using cached runtime image: {runtime_tag}", file=sys.stderr)
            return runtime_tag

        agent_display = self.agent.config.display_name
        print(f"Installing {agent_display} (first run only)...", file=sys.stderr)

        with tempfile.TemporaryDirectory() as tmpdir:
            dockerfile_path = Path(tmpdir) / "Dockerfile"
            dockerfile_path.write_text(layer_content)

            build_args = {"BASE_IMAGE": base_image}
            try:
                self.build_image(dockerfile_path, runtime_tag, Path(tmpdir), build_args)
            except Exception:
                engine_name = self._engine.binary
                print(
                    f"\n{agent_display} installation failed. This usually means:\n"
                    "  - Network connectivity issues (check your connection)\n"
                    f"  - {engine_name.capitalize()} not running "
                    f"(run '{engine_name} machine start' if applicable)\n"
                    "  - Disk space issues\n",
                    file=sys.stderr,
                )
                raise

        print(f"{agent_display} installed successfully.", file=sys.stderr)
        return runtime_tag

    def ensure_custom_image(
        self,
        config: PaudeConfig,
        force_rebuild: bool = False,
        workspace: Path | None = None,
    ) -> str:
        """Ensure a custom workspace image is available.

        Returns:
            Image tag to use.
        """
        import sys

        entrypoint = resolve_entrypoint(self.script_dir)
        agent_name = self.agent.config.name if self.agent else None
        effective_base = config.base_image
        if not effective_base and self.agent and self.agent.config.default_base_image:
            effective_base = self.agent.config.default_base_image
        config_hash = compute_config_hash(
            config.config_file,
            config.dockerfile,
            effective_base,
            entrypoint,
            self.version,
            agent_name=agent_name,
        )

        if self.platform:
            arch = self.platform.split("/")[-1]
            tag = f"paude-workspace:{config_hash}-{arch}"
        else:
            tag = f"paude-workspace:{config_hash}"

        if not force_rebuild and self._engine.image_exists(tag):
            print(f"Using cached workspace image: {tag}", file=sys.stderr)
            return tag

        print("Building workspace image...", file=sys.stderr)
        base_image, using_default = self._resolve_custom_base(config, config_hash)
        dockerfile_content = generate_dockerfile_content(
            config, using_default, agent=self.agent
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "Dockerfile").write_text(dockerfile_content)

            if not using_default:
                copy_entrypoints(entrypoint, Path(tmpdir))

            if config.features:
                copy_features_cache(Path(tmpdir))

            build_args = {"BASE_IMAGE": base_image}
            self.build_image(Path(tmpdir) / "Dockerfile", tag, Path(tmpdir), build_args)

        print(f"Build complete (cached as {tag})", file=sys.stderr)
        return tag

    def _resolve_custom_base(
        self, config: PaudeConfig, config_hash: str
    ) -> tuple[str, bool]:
        """Resolve the base image for custom workspace builds."""
        import sys

        if config.dockerfile:
            if not config.dockerfile.exists():
                raise FileNotFoundError(f"Dockerfile not found: {config.dockerfile}")
            user_image = f"paude-user-base:{config_hash}"
            build_context = config.build_context or config.dockerfile.parent
            print(f"  → Building from: {config.dockerfile}", file=sys.stderr)
            user_build_args = dict(config.build_args)
            self.build_image(
                config.dockerfile, user_image, build_context, user_build_args
            )
            print("  → Adding paude requirements...", file=sys.stderr)
            return user_image, False
        elif config.base_image:
            print(f"  → Using base: {config.base_image}", file=sys.stderr)
            return config.base_image, False
        elif self.agent and self.agent.config.default_base_image:
            base = self.agent.config.default_base_image
            print(f"  → Using agent default base: {base}", file=sys.stderr)
            return base, False
        else:
            base_image = self.ensure_default_image()
            print(f"  → Using default paude image: {base_image}", file=sys.stderr)
            return base_image, True

    def ensure_proxy_image(self, force_rebuild: bool = False) -> str:
        """Ensure the proxy image is available.

        Returns:
            Image tag to use.
        """
        import sys

        if self.dev_mode and self.script_dir:
            if self.platform:
                arch = self.platform.split("/")[-1]
                tag = f"paude-proxy-centos10:latest-{arch}"
            else:
                tag = "paude-proxy-centos10:latest"
            if force_rebuild or not self._engine.image_exists(tag):
                print(f"Building {tag} image...", file=sys.stderr)
                dockerfile = self.script_dir / "containers" / "proxy" / "Dockerfile"
                context = self.script_dir / "containers" / "proxy"
                self.build_image(dockerfile, tag, context)
            return tag
        else:
            tag = f"{self.registry}/paude-proxy-centos10:{self.version}"
            if not self._engine.image_exists(tag):
                print(f"Pulling {tag}...", file=sys.stderr)
                try:
                    self._engine.run(
                        "pull", "--platform", self.platform, tag, capture=False
                    )
                except Exception:
                    engine_name = self._engine.binary
                    print(
                        f"Check your network connection or run '{engine_name} login' "
                        "if authentication is required.",
                        file=sys.stderr,
                    )
                    raise
            return tag

    def build_image(
        self,
        dockerfile: Path,
        tag: str,
        context: Path,
        build_args: dict[str, str] | None = None,
    ) -> None:
        """Build a container image.

        When the engine is remote, the build context is transferred to the
        remote host via tar pipe before building.
        """
        if self._engine.is_remote:
            self._build_image_remote(dockerfile, tag, context, build_args)
            return

        cmd = ["build", "-f", str(dockerfile), "-t", tag]

        if self.platform:
            cmd.extend(["--platform", self.platform])
        if build_args:
            for key, value in build_args.items():
                cmd.extend(["--build-arg", f"{key}={value}"])
        cmd.append(str(context))
        self._engine.run(*cmd, capture=False)

    def _build_image_remote(
        self,
        dockerfile: Path,
        tag: str,
        context: Path,
        build_args: dict[str, str] | None = None,
    ) -> None:
        """Build an image on a remote host by transferring the build context."""
        import subprocess

        from paude.transport.ssh import SshTransport

        transport = self._engine.transport
        if not isinstance(transport, SshTransport):
            raise RuntimeError("Remote build requires SshTransport")

        # Create temp dir on remote for the build context
        result = transport.run(
            ["mktemp", "-d", "/tmp/paude-build-XXXX"],  # noqa: S108
            check=True,
        )
        remote_dir = result.stdout.strip()

        try:
            # Transfer build context via tar pipe
            tar_cmd = ["tar"]
            if is_macos():
                tar_cmd.append("--no-mac-metadata")
            tar_cmd.extend(["-cf", "-", "-C", str(context), "."])
            untar_cmd = [
                *transport.ssh_base(),
                "--",
                "tar",
                "--warning=no-unknown-keyword",
                "-xf",
                "-",
                "-C",
                remote_dir,
            ]
            tar_proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
            try:
                untar_proc = subprocess.Popen(untar_cmd, stdin=tar_proc.stdout)
                if tar_proc.stdout:
                    tar_proc.stdout.close()
                untar_proc.wait()
            finally:
                tar_proc.wait()

            if untar_proc.returncode != 0:
                raise RuntimeError("Failed to transfer build context to remote")

            # If dockerfile is inside context, use relative path on remote
            try:
                rel_dockerfile = dockerfile.relative_to(context)
                remote_dockerfile = f"{remote_dir}/{rel_dockerfile}"
            except ValueError:
                # Dockerfile outside context — transfer it separately
                remote_dockerfile = f"{remote_dir}/Dockerfile.paude"
                with open(dockerfile, "rb") as f:
                    transport.run(
                        ["tee", remote_dockerfile],
                        input=f.read().decode(),
                        check=True,
                    )

            # Build on remote
            cmd = ["build", "-f", remote_dockerfile, "-t", tag]
            if self.platform:
                cmd.extend(["--platform", self.platform])
            if build_args:
                for key, value in build_args.items():
                    cmd.extend(["--build-arg", f"{key}={value}"])
            cmd.append(remote_dir)
            self._engine.run(*cmd, capture=False)
        finally:
            # Clean up remote temp dir
            transport.run(["rm", "-rf", remote_dir], check=False)
