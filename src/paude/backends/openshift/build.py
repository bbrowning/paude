"""Build orchestration for OpenShift binary builds."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import (
    BuildFailedError,
    OcTimeoutError,
    OpenShiftError,
)
from paude.backends.openshift.oc import OcClient


class BuildOrchestrator:
    """Orchestrates OpenShift binary builds for container images.

    Handles creating BuildConfigs, starting builds, waiting for completion,
    and retrieving image references.
    """

    def __init__(
        self,
        oc: OcClient,
        namespace: str,
        config: OpenShiftConfig,
    ) -> None:
        """Initialize the BuildOrchestrator.

        Args:
            oc: OcClient instance for running oc commands.
            namespace: Kubernetes namespace for builds.
            config: OpenShift configuration with build resources.
        """
        self._oc = oc
        self._namespace = namespace
        self._config = config

    def create_build_config(
        self,
        config_hash: str,
        name_prefix: str = "paude",
    ) -> None:
        """Create a BuildConfig and ImageStream for binary builds.

        If the BuildConfig already exists, this is a no-op.

        Args:
            config_hash: Hash of the configuration for naming.
            name_prefix: Prefix for build config name (default: "paude").
        """
        bc_name = f"{name_prefix}-{config_hash}"

        result = self._oc.run(
            "get", "buildconfig", bc_name,
            "-n", self._namespace,
            check=False,
        )
        if result.returncode == 0:
            print(
                f"BuildConfig/{bc_name} already exists, reusing...",
                file=sys.stderr,
            )
            return

        print(f"Creating BuildConfig/{bc_name}...", file=sys.stderr)

        is_spec: dict[str, Any] = {
            "apiVersion": "image.openshift.io/v1",
            "kind": "ImageStream",
            "metadata": {
                "name": bc_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude",
                    "paude.io/config-hash": config_hash,
                },
            },
        }

        bc_spec: dict[str, Any] = {
            "apiVersion": "build.openshift.io/v1",
            "kind": "BuildConfig",
            "metadata": {
                "name": bc_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude",
                    "paude.io/config-hash": config_hash,
                },
            },
            "spec": {
                "output": {
                    "to": {
                        "kind": "ImageStreamTag",
                        "name": f"{bc_name}:latest",
                    },
                },
                "source": {
                    "type": "Binary",
                },
                "strategy": {
                    "type": "Docker",
                    "dockerStrategy": {},
                },
                "resources": self._config.build_resources,
            },
        }

        self._oc.run("apply", "-f", "-", input_data=json.dumps(is_spec))
        self._oc.run("apply", "-f", "-", input_data=json.dumps(bc_spec))

    def start_binary_build(
        self,
        config_hash: str,
        context_dir: Path,
        session_name: str | None = None,
        name_prefix: str = "paude",
    ) -> str:
        """Start a binary build and return the build name.

        Args:
            config_hash: Hash of the configuration for naming.
            context_dir: Path to the build context directory.
            session_name: Optional session name to label the build with.
            name_prefix: Prefix for build config name (default: "paude").

        Returns:
            Name of the started build (e.g., "paude-abc123-1").
        """
        bc_name = f"{name_prefix}-{config_hash}"

        print(
            f"Starting build from {context_dir}...",
            file=sys.stderr,
        )

        result = self._oc.run(
            "start-build", bc_name,
            f"--from-dir={context_dir}",
            "-n", self._namespace,
            timeout=120,
        )

        build_name = result.stdout.strip()
        if build_name.startswith("build.build.openshift.io/"):
            build_name = build_name.split("/")[1]
        elif build_name.startswith("build/"):
            build_name = build_name.split("/")[1]

        build_name = build_name.strip('"').replace(" started", "")
        print(f"Build {build_name} started", file=sys.stderr)

        if session_name:
            self._oc.run(
                "label", "build", build_name,
                f"paude.io/session-name={session_name}",
                "-n", self._namespace,
                check=False,
            )

        return build_name

    def wait_for_build(self, build_name: str, timeout: int = 600) -> None:
        """Wait for a build to complete, streaming logs.

        Args:
            build_name: Name of the build to wait for.
            timeout: Timeout in seconds.

        Raises:
            BuildFailedError: If the build fails.
            OcTimeoutError: If the build times out.
        """
        print(f"Waiting for build {build_name} to complete...", file=sys.stderr)
        print("--- Build Logs ---", file=sys.stderr)

        log_proc = subprocess.Popen(
            ["oc", "logs", "-f", f"build/{build_name}", "-n", self._namespace]
            + (["--context", self._config.context] if self._config.context else []),
            stdout=sys.stderr,
            stderr=sys.stderr,
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._oc.run(
                "get", "build", build_name,
                "-n", self._namespace,
                "-o", "jsonpath={.status.phase}",
                check=False,
            )
            if result.returncode != 0:
                time.sleep(2)
                continue

            phase = result.stdout.strip()

            if phase == "Complete":
                log_proc.terminate()
                print("--- End Build Logs ---", file=sys.stderr)
                print(f"Build {build_name} completed successfully.", file=sys.stderr)
                return

            if phase in ("Failed", "Error", "Cancelled"):
                log_proc.terminate()
                print("--- End Build Logs ---", file=sys.stderr)

                reason_result = self._oc.run(
                    "get", "build", build_name,
                    "-n", self._namespace,
                    "-o", "jsonpath={.status.message}",
                    check=False,
                )
                reason = (
                    reason_result.stdout.strip()
                    if reason_result.returncode == 0
                    else phase
                )

                logs_result = self._oc.run(
                    "logs", f"build/{build_name}",
                    "-n", self._namespace,
                    "--tail=50",
                    check=False,
                )
                logs = logs_result.stdout if logs_result.returncode == 0 else None

                raise BuildFailedError(build_name, reason, logs)

            time.sleep(5)

        log_proc.terminate()
        raise OcTimeoutError(
            f"Build {build_name} did not complete within {timeout} seconds"
        )

    def get_imagestream_reference(
        self,
        config_hash: str,
        name_prefix: str = "paude",
    ) -> str:
        """Get the internal image reference from an ImageStream.

        Args:
            config_hash: Hash of the configuration for naming.
            name_prefix: Prefix for imagestream name (default: "paude").

        Returns:
            Internal image reference for pod image pulls.
        """
        is_name = f"{name_prefix}-{config_hash}"

        result = self._oc.run(
            "get", "imagestream", is_name,
            "-n", self._namespace,
            "-o", "jsonpath={.status.dockerImageRepository}",
        )
        repo = result.stdout.strip()
        if not repo:
            repo = (
                f"image-registry.openshift-image-registry.svc:5000/"
                f"{self._namespace}/{is_name}"
            )

        return f"{repo}:latest"

    def ensure_image_via_build(
        self,
        config: Any,
        workspace: Path,
        script_dir: Path | None = None,
        force_rebuild: bool = False,
        session_name: str | None = None,
    ) -> str:
        """Ensure an image is available via OpenShift binary build.

        Args:
            config: PaudeConfig (or None for default image).
            workspace: Workspace directory.
            script_dir: Path to paude script directory (for dev mode).
            force_rebuild: Force rebuild even if image exists.
            session_name: Optional session name to label the build with.

        Returns:
            Internal image reference for pod image pulls.
        """
        from paude.container.image import prepare_build_context

        if config is None:
            from paude.config.models import PaudeConfig
            config = PaudeConfig(config_file=None)

        build_ctx = prepare_build_context(
            config,
            workspace=workspace,
            script_dir=script_dir,
            platform="linux/amd64",
            for_remote_build=True,
        )

        try:
            config_hash = build_ctx.config_hash
            is_name = f"paude-{config_hash}"

            if not force_rebuild:
                result = self._oc.run(
                    "get", "imagestreamtag",
                    f"{is_name}:latest",
                    "-n", self._namespace,
                    check=False,
                )
                if result.returncode == 0:
                    print(
                        f"Image {is_name}:latest already exists, reusing...",
                        file=sys.stderr,
                    )
                    return self.get_imagestream_reference(config_hash)

            self.create_build_config(config_hash)

            build_name = self.start_binary_build(
                config_hash,
                build_ctx.context_dir,
                session_name=session_name,
            )

            self.wait_for_build(build_name)

            return self.get_imagestream_reference(config_hash)

        finally:
            shutil.rmtree(build_ctx.context_dir, ignore_errors=True)

    def ensure_proxy_image_via_build(
        self,
        script_dir: Path,
        force_rebuild: bool = False,
        session_name: str | None = None,
    ) -> str:
        """Ensure the proxy image is available via OpenShift binary build.

        Args:
            script_dir: Path to paude script directory containing containers/proxy/.
            force_rebuild: Force rebuild even if image exists.
            session_name: Optional session name to label the build with.

        Returns:
            Internal image reference for proxy pod image pulls.
        """
        name_prefix = "paude-proxy"

        proxy_dir = script_dir / "containers" / "proxy"
        if not proxy_dir.is_dir():
            raise OpenShiftError(
                f"Proxy container directory not found: {proxy_dir}"
            )

        dockerfile_path = proxy_dir / "Dockerfile"
        if not dockerfile_path.exists():
            raise OpenShiftError(
                f"Proxy Dockerfile not found: {dockerfile_path}"
            )

        proxy_files = ["Dockerfile", "squid.conf", "entrypoint.sh"]
        hash_content = ""
        for filename in sorted(proxy_files):
            filepath = proxy_dir / filename
            if filepath.exists():
                hash_content += filepath.read_text()
        config_hash = hashlib.sha256(hash_content.encode()).hexdigest()[:12]
        is_name = f"{name_prefix}-{config_hash}"

        if not force_rebuild:
            result = self._oc.run(
                "get", "imagestreamtag",
                f"{is_name}:latest",
                "-n", self._namespace,
                check=False,
            )
            if result.returncode == 0:
                print(
                    f"Proxy image {is_name}:latest already exists, reusing...",
                    file=sys.stderr,
                )
                return self.get_imagestream_reference(config_hash, name_prefix)

        context_dir = Path(tempfile.mkdtemp(prefix="paude-proxy-build-"))
        try:
            for filename in proxy_files:
                src = proxy_dir / filename
                if src.exists():
                    shutil.copy2(src, context_dir / filename)

            self.create_build_config(config_hash, name_prefix)

            build_name = self.start_binary_build(
                config_hash,
                context_dir,
                session_name=session_name,
                name_prefix=name_prefix,
            )

            self.wait_for_build(build_name)

            return self.get_imagestream_reference(config_hash, name_prefix)

        finally:
            shutil.rmtree(context_dir, ignore_errors=True)

    def delete_session_builds(self, session_name: str) -> None:
        """Delete builds associated with a session.

        Args:
            session_name: Session name to identify builds to delete.
        """
        self._oc.run(
            "delete", "build",
            "-n", self._namespace,
            "-l", f"paude.io/session-name={session_name}",
            check=False,
        )
