"""OpenShift backend implementation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.base import Session, SessionConfig

CLAUDE_EXCLUDES = [
    # Session-specific files (not useful on remote)
    "/debug",          # Debug logs - session specific
    "/file-history",   # File history - session specific
    "/history.jsonl",  # Command history - session specific
    "/paste-cache",    # Paste cache - session specific
    "/session-env",    # Session environment - session specific
    "/shell-snapshots",  # Shell snapshots - session specific
    "/stats-cache.json",  # Stats cache - session specific
    "/tasks",          # Task state - session specific
    "/todos",          # Todo state - session specific
    "/projects",       # Project state - may contain session data
    # Leading / anchors to root - only matches top-level, not nested dirs
    "/cache",          # General cache (NOT plugins/cache with plugin files)
    "/.git",           # Git metadata (if user has versioned ~/.claude)
]


class OpenShiftError(Exception):
    """Base exception for OpenShift backend errors."""

    pass


class OcNotInstalledError(OpenShiftError):
    """The oc CLI is not installed."""

    pass


class OcNotLoggedInError(OpenShiftError):
    """Not logged in to OpenShift cluster."""

    pass


class OcTimeoutError(OpenShiftError):
    """The oc CLI command timed out."""

    pass


class PodNotFoundError(OpenShiftError):
    """Pod not found."""

    pass


class PodNotReadyError(OpenShiftError):
    """Pod is not ready."""

    pass


class NamespaceNotFoundError(OpenShiftError):
    """Namespace does not exist."""

    pass


class BuildFailedError(OpenShiftError):
    """OpenShift binary build failed."""

    def __init__(
        self, build_name: str, reason: str, logs: str | None = None
    ) -> None:
        self.build_name = build_name
        self.reason = reason
        self.logs = logs
        message = f"Build '{build_name}' failed: {reason}"
        if logs:
            message += f"\n\nBuild logs:\n{logs}"
        super().__init__(message)


class SessionExistsError(OpenShiftError):
    """Session with this name already exists."""

    pass


class SessionNotFoundError(OpenShiftError):
    """Session not found."""

    pass


@dataclass
class OpenShiftConfig:
    """Configuration for OpenShift backend.

    Attributes:
        context: Kubeconfig context to use (None for current context).
        namespace: Namespace for paude resources (None for current namespace).
        resources: Resource requests/limits for session pods.
        build_resources: Resource requests/limits for build pods.
    """

    context: str | None = None
    namespace: str | None = None  # None means use current namespace
    resources: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "requests": {"cpu": "1", "memory": "4Gi"},
        "limits": {"cpu": "4", "memory": "8Gi"},
    })
    build_resources: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "requests": {"cpu": "1", "memory": "2Gi"},
        "limits": {"cpu": "4", "memory": "8Gi"},
    })


def _generate_session_name(workspace: Path) -> str:
    """Generate a session name from workspace path.

    Args:
        workspace: Workspace path.

    Returns:
        Session name in format "{dir-name}-{hash}".
    """
    dir_name = workspace.name.lower()
    # Sanitize for Kubernetes naming (lowercase, alphanumeric, dashes)
    sanitized = "".join(c if c.isalnum() else "-" for c in dir_name)
    sanitized = sanitized.strip("-")[:20]  # Limit length
    if not sanitized:
        sanitized = "session"

    # Add hash for uniqueness
    path_hash = hashlib.sha256(str(workspace).encode()).hexdigest()[:8]
    return f"{sanitized}-{path_hash}"


def _encode_path(path: Path) -> str:
    """Base64 encode a path for storing in labels.

    Args:
        path: Path to encode.

    Returns:
        Base64-encoded path string.
    """
    return base64.b64encode(str(path).encode()).decode()


def _decode_path(encoded: str) -> Path:
    """Decode a base64-encoded path.

    Args:
        encoded: Base64-encoded path string.

    Returns:
        Decoded Path object.
    """
    return Path(base64.b64decode(encoded.encode()).decode())


class OpenShiftBackend:
    """OpenShift container backend.

    This backend runs Claude in pods on an OpenShift cluster. Sessions are
    persistent and can survive network disconnections using tmux.
    """

    def __init__(self, config: OpenShiftConfig | None = None) -> None:
        """Initialize the OpenShift backend.

        Args:
            config: OpenShift configuration. Defaults to OpenShiftConfig().
        """
        self._config = config or OpenShiftConfig()
        self._syncer: Any = None
        self._resolved_namespace: str | None = None

    @property
    def namespace(self) -> str:
        """Get the resolved namespace.

        If namespace is not explicitly configured, uses the current namespace
        from the kubeconfig context.

        Returns:
            Resolved namespace name.
        """
        if self._resolved_namespace is not None:
            return self._resolved_namespace

        if self._config.namespace:
            self._resolved_namespace = self._config.namespace
        else:
            # Get current namespace from kubeconfig
            self._resolved_namespace = self._get_current_namespace()

        return self._resolved_namespace

    # Default timeout for oc commands (seconds)
    OC_DEFAULT_TIMEOUT = 30
    # Timeout for oc exec operations (may be slow after pod restart)
    OC_EXEC_TIMEOUT = 120
    # Timeout for rsync operations (5 minutes - large workspaces take time)
    RSYNC_TIMEOUT = 300
    # Number of retries for rsync on timeout
    RSYNC_MAX_RETRIES = 3

    def _run_oc(
        self,
        *args: str,
        capture: bool = True,
        check: bool = True,
        input_data: str | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run an oc command.

        Args:
            *args: Command arguments (without 'oc').
            capture: Capture output (default True).
            check: Raise on non-zero exit (default True).
            input_data: Optional input to pass to stdin.
            timeout: Timeout in seconds (default OC_DEFAULT_TIMEOUT).
                     Use None to inherit class default, 0 for no timeout.

        Returns:
            CompletedProcess result.

        Raises:
            OcNotInstalledError: If oc is not installed.
            OcTimeoutError: If command times out.
            OpenShiftError: If command fails and check=True.
        """
        cmd = ["oc"]

        # Add context if specified
        if self._config.context:
            cmd.extend(["--context", self._config.context])

        cmd.extend(args)

        # Determine timeout value
        if timeout is None:
            timeout_value: float | None = self.OC_DEFAULT_TIMEOUT
        elif timeout == 0:
            timeout_value = None  # No timeout
        else:
            timeout_value = timeout

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                input=input_data,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired:
            cmd_str = " ".join(cmd)
            raise OcTimeoutError(
                f"oc command timed out after {timeout_value}s: {cmd_str}\n"
                "This may indicate network issues connecting to the cluster.\n"
                "Check your cluster connectivity and try: oc status"
            ) from None
        except FileNotFoundError as e:
            raise OcNotInstalledError(
                "oc CLI not found. Install it from your OpenShift cluster or "
                "run: brew install openshift-cli"
            ) from e

        if check and result.returncode != 0:
            stderr = result.stderr if capture else ""
            if "error: You must be logged in" in stderr:
                raise OcNotLoggedInError(
                    "Not logged in to OpenShift. Run: oc login <cluster-url>"
                )
            raise OpenShiftError(f"oc command failed: {stderr}")

        return result

    def _rsync_with_retry(
        self,
        source: str,
        dest: str,
        namespace: str,
        exclude_args: list[str],
        verbose: bool = False,
        delete: bool = False,
    ) -> bool:
        """Run oc rsync with retry logic for timeouts.

        Args:
            source: Source path (local or pod:path format).
            dest: Destination path (local or pod:path format).
            namespace: Kubernetes namespace.
            exclude_args: List of --exclude arguments.
            verbose: Whether to show rsync output (default False).
            delete: Whether to delete files not in source (default False).

        Returns:
            True if sync succeeded, False if all retries failed.
        """
        for attempt in range(1, self.RSYNC_MAX_RETRIES + 1):
            try:
                # Build rsync args
                rsync_args = [
                    "rsync",
                    "--progress",
                    source,
                    dest,
                    "-n", namespace,
                    "--no-perms",
                ]
                if delete:
                    rsync_args.append("--delete")
                rsync_args.extend(exclude_args)

                # Always capture output so we can report errors
                # When verbose, we'll print stdout/stderr after
                result = self._run_oc(
                    *rsync_args,
                    timeout=self.RSYNC_TIMEOUT,
                    capture=True,
                    check=False,
                )

                # Show output when verbose is enabled
                if verbose and result.stdout:
                    print(result.stdout, file=sys.stderr)

                # Check for rsync failure
                if result.returncode != 0:
                    print(
                        f"Rsync failed: {result.stderr.strip() or 'unknown error'}",
                        file=sys.stderr,
                    )
                    return False

                return True
            except OcTimeoutError:
                retries = self.RSYNC_MAX_RETRIES
                if attempt < retries:
                    print(
                        f"Rsync timed out (attempt {attempt}/{retries}), retrying...",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Rsync failed after {retries} attempts",
                        file=sys.stderr,
                    )
                    return False
        return False

    def _check_connection(self) -> bool:
        """Check if logged in to OpenShift.

        Returns:
            True if logged in.

        Raises:
            OcNotLoggedInError: If not logged in.
        """
        result = self._run_oc("whoami", check=False)
        if result.returncode != 0:
            raise OcNotLoggedInError(
                "Not logged in to OpenShift. Run: oc login <cluster-url>"
            )
        return True

    def _get_current_namespace(self) -> str:
        """Get the current namespace from oc config.

        Returns:
            Current namespace name.
        """
        result = self._run_oc(
            "config", "view", "--minify", "-o",
            "jsonpath={.contexts[0].context.namespace}"
        )
        ns = result.stdout.strip()
        return ns if ns else "default"

    def _verify_namespace(self) -> None:
        """Verify the target namespace exists.

        Raises:
            NamespaceNotFoundError: If the namespace does not exist.
        """
        ns = self.namespace

        # Check if namespace exists
        result = self._run_oc("get", "namespace", ns, check=False)
        if result.returncode != 0:
            raise NamespaceNotFoundError(
                f"Namespace '{ns}' does not exist. "
                f"Please create it or switch to an existing namespace."
            )

    def _create_build_config(self, config_hash: str) -> None:
        """Create a BuildConfig and ImageStream for binary builds.

        If the BuildConfig already exists, this is a no-op.

        Args:
            config_hash: Hash of the configuration for naming.
        """
        ns = self.namespace
        bc_name = f"paude-{config_hash}"

        result = self._run_oc(
            "get", "buildconfig", bc_name,
            "-n", ns,
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
                "namespace": ns,
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
                "namespace": ns,
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

        self._run_oc("apply", "-f", "-", input_data=json.dumps(is_spec))
        self._run_oc("apply", "-f", "-", input_data=json.dumps(bc_spec))

    def _start_binary_build(
        self,
        config_hash: str,
        context_dir: Path,
        session_name: str | None = None,
    ) -> str:
        """Start a binary build and return the build name.

        Args:
            config_hash: Hash of the configuration for naming.
            context_dir: Path to the build context directory.
            session_name: Optional session name to label the build with.

        Returns:
            Name of the started build (e.g., "paude-abc123-1").
        """
        ns = self.namespace
        bc_name = f"paude-{config_hash}"

        print(
            f"Starting build from {context_dir}...",
            file=sys.stderr,
        )

        result = self._run_oc(
            "start-build", bc_name,
            f"--from-dir={context_dir}",
            "-n", ns,
            timeout=120,
        )

        build_name = result.stdout.strip()
        if build_name.startswith("build.build.openshift.io/"):
            build_name = build_name.split("/")[1]
        elif build_name.startswith("build/"):
            build_name = build_name.split("/")[1]

        build_name = build_name.strip('"').replace(" started", "")
        print(f"Build {build_name} started", file=sys.stderr)

        # Label the build with session name for cleanup
        if session_name:
            self._run_oc(
                "label", "build", build_name,
                f"paude.io/session-name={session_name}",
                "-n", ns,
                check=False,
            )

        return build_name

    def _wait_for_build(
        self,
        build_name: str,
        timeout: int = 600,
    ) -> None:
        """Wait for a build to complete, streaming logs.

        Args:
            build_name: Name of the build to wait for.
            timeout: Timeout in seconds.

        Raises:
            BuildFailedError: If the build fails.
            OcTimeoutError: If the build times out.
        """
        ns = self.namespace

        print(f"Waiting for build {build_name} to complete...", file=sys.stderr)
        print("--- Build Logs ---", file=sys.stderr)

        log_proc = subprocess.Popen(
            ["oc", "logs", "-f", f"build/{build_name}", "-n", ns]
            + (["--context", self._config.context] if self._config.context else []),
            stdout=sys.stderr,
            stderr=sys.stderr,
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._run_oc(
                "get", "build", build_name,
                "-n", ns,
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

                reason_result = self._run_oc(
                    "get", "build", build_name,
                    "-n", ns,
                    "-o", "jsonpath={.status.message}",
                    check=False,
                )
                if reason_result.returncode == 0:
                    reason = reason_result.stdout.strip()
                else:
                    reason = phase

                logs_result = self._run_oc(
                    "logs", f"build/{build_name}",
                    "-n", ns,
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

    def _get_imagestream_reference(self, config_hash: str) -> str:
        """Get the internal image reference from an ImageStream.

        Args:
            config_hash: Hash of the configuration for naming.

        Returns:
            Internal image reference for pod image pulls.
        """
        ns = self.namespace
        is_name = f"paude-{config_hash}"

        result = self._run_oc(
            "get", "imagestream", is_name,
            "-n", ns,
            "-o", "jsonpath={.status.dockerImageRepository}",
        )
        repo = result.stdout.strip()
        if not repo:
            repo = f"image-registry.openshift-image-registry.svc:5000/{ns}/{is_name}"

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

        This method:
        1. Prepares a build context with Dockerfile and source
        2. Creates a BuildConfig/ImageStream if needed
        3. Runs a binary build in the cluster
        4. Returns the internal image reference

        Args:
            config: PaudeConfig (or None for default image).
            workspace: Workspace directory.
            script_dir: Path to paude script directory (for dev mode).
            force_rebuild: Force rebuild even if image exists.
            session_name: Optional session name to label the build with.

        Returns:
            Internal image reference for pod image pulls.
        """
        import shutil

        from paude.container.image import prepare_build_context

        ns = self.namespace

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
                result = self._run_oc(
                    "get", "imagestreamtag",
                    f"{is_name}:latest",
                    "-n", ns,
                    check=False,
                )
                if result.returncode == 0:
                    print(
                        f"Image {is_name}:latest already exists, reusing...",
                        file=sys.stderr,
                    )
                    return self._get_imagestream_reference(config_hash)

            self._create_build_config(config_hash)

            build_name = self._start_binary_build(
                config_hash,
                build_ctx.context_dir,
                session_name=session_name,
            )

            self._wait_for_build(build_name)

            return self._get_imagestream_reference(config_hash)

        finally:
            shutil.rmtree(build_ctx.context_dir, ignore_errors=True)

    def _ensure_network_policy(self, session_id: str) -> None:
        """Ensure a NetworkPolicy exists that restricts egress traffic for this session.

        Creates a NetworkPolicy that:
        - Allows egress to DNS (UDP/TCP 53)
        - Allows egress to this session's proxy pod on port 3128
        - Denies all other egress traffic

        The paude pod can ONLY reach DNS and the squid proxy. The proxy handles
        domain-based filtering via squid.conf.

        Args:
            session_id: The session ID to scope the policy to.
        """
        ns = self.namespace
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace {ns}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": ns,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": session_id,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    # Allow DNS to any pod in any namespace
                    # Both namespaceSelector: {} AND podSelector: {} are required
                    # together to match "any pod" for cross-namespace DNS access
                    {
                        "to": [
                            {
                                "namespaceSelector": {},
                                "podSelector": {},
                            },
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                            {"protocol": "UDP", "port": 5353},
                            {"protocol": "TCP", "port": 5353},
                        ],
                    },
                    # Allow access to THIS session's proxy pod only
                    {
                        "to": [
                            {
                                "podSelector": {
                                    "matchLabels": {
                                        "app": "paude-proxy",
                                        "paude.io/session-name": session_id,
                                    },
                                },
                            },
                        ],
                        "ports": [
                            {"protocol": "TCP", "port": 3128},
                        ],
                    },
                ],
            },
        }

        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def _ensure_network_policy_permissive(self, session_id: str) -> None:
        """Ensure a permissive NetworkPolicy exists for this session (allow all egress).

        Used when --allow-network is specified.

        Args:
            session_id: The session ID to scope the policy to.
        """
        ns = self.namespace
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace {ns}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": ns,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": session_id,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    {},  # Empty rule allows all egress
                ],
            },
        }

        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def _create_proxy_deployment(
        self,
        session_name: str,
        proxy_image: str,
    ) -> None:
        """Create a Deployment for the squid proxy pod.

        The proxy pod handles domain-based filtering using squid.conf.
        The paude container routes all HTTP/HTTPS traffic through this proxy.

        Args:
            session_name: Session name for labeling.
            proxy_image: Container image for the proxy.
        """
        ns = self.namespace
        deployment_name = f"paude-proxy-{session_name}"

        print(
            f"Creating Deployment/{deployment_name} in namespace {ns}...",
            file=sys.stderr,
        )

        deployment_spec: dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": deployment_name,
                "namespace": ns,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app": "paude-proxy",
                        "paude.io/session-name": session_name,
                    },
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "paude-proxy",
                            "paude.io/session-name": session_name,
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "proxy",
                                "image": proxy_image,
                                "imagePullPolicy": "Always",
                                "ports": [{"containerPort": 3128}],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "500m", "memory": "256Mi"},
                                },
                            },
                        ],
                    },
                },
            },
        }

        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(deployment_spec),
        )

    def _create_proxy_service(
        self,
        session_name: str,
    ) -> str:
        """Create a Service for the squid proxy pod.

        Args:
            session_name: Session name for labeling.

        Returns:
            The service hostname (e.g., "paude-proxy-{session_name}").
        """
        ns = self.namespace
        service_name = f"paude-proxy-{session_name}"

        print(
            f"Creating Service/{service_name} in namespace {ns}...",
            file=sys.stderr,
        )

        service_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": ns,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "selector": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
                "ports": [
                    {
                        "port": 3128,
                        "targetPort": 3128,
                        "protocol": "TCP",
                    },
                ],
            },
        }

        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(service_spec),
        )

        return service_name

    def _wait_for_proxy_ready(
        self,
        session_name: str,
        timeout: int = 120,
    ) -> None:
        """Wait for the proxy deployment to be ready.

        Args:
            session_name: Session name.
            timeout: Timeout in seconds.
        """
        deployment_name = f"paude-proxy-{session_name}"
        ns = self.namespace

        print(
            f"Waiting for Deployment/{deployment_name} to be ready...",
            file=sys.stderr,
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._run_oc(
                "get", "deployment", deployment_name,
                "-n", ns,
                "-o", "jsonpath={.status.readyReplicas}",
                check=False,
            )

            if result.returncode == 0:
                ready = result.stdout.strip()
                if ready and int(ready) > 0:
                    print(
                        f"Deployment/{deployment_name} is ready.",
                        file=sys.stderr,
                    )
                    return

            time.sleep(2)

        print(
            f"Warning: Deployment/{deployment_name} not ready after {timeout}s",
            file=sys.stderr,
        )

    def _delete_proxy_resources(self, session_name: str) -> None:
        """Delete proxy Deployment and Service for a session.

        Args:
            session_name: Session name.
        """
        ns = self.namespace
        deployment_name = f"paude-proxy-{session_name}"
        service_name = f"paude-proxy-{session_name}"

        print(f"Deleting Deployment/{deployment_name}...", file=sys.stderr)
        self._run_oc(
            "delete", "deployment", deployment_name,
            "-n", ns,
            "--grace-period=0",
            check=False,
        )

        print(f"Deleting Service/{service_name}...", file=sys.stderr)
        self._run_oc(
            "delete", "service", service_name,
            "-n", ns,
            check=False,
        )

    def _delete_session_builds(self, session_name: str) -> None:
        """Delete Build objects labeled for a session.

        Args:
            session_name: Session name.
        """
        ns = self.namespace
        print(
            f"Deleting Build objects for session '{session_name}'...",
            file=sys.stderr,
        )
        self._run_oc(
            "delete", "build",
            "-n", ns,
            "-l", f"paude.io/session-name={session_name}",
            check=False,
        )

    def _ensure_proxy_network_policy(self, session_name: str) -> None:
        """Create a NetworkPolicy that allows all egress for the proxy pod.

        The proxy pod needs unrestricted egress to reach the internet.
        Domain-based filtering is handled by squid.conf, not NetworkPolicy.

        Args:
            session_name: Session name for labeling.
        """
        ns = self.namespace
        policy_name = f"paude-proxy-egress-{session_name}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace {ns}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": ns,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude-proxy",
                        "paude.io/session-name": session_name,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    {},  # Empty rule allows all egress
                ],
            },
        }

        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def _wait_for_pod_ready(
        self,
        pod_name: str,
        timeout: int = 300,
    ) -> None:
        """Wait for a pod to be in Running state.

        Args:
            pod_name: Name of the pod.
            timeout: Timeout in seconds.

        Raises:
            PodNotReadyError: If pod is not ready within timeout.
        """
        start_time = time.time()
        ns = self.namespace

        while time.time() - start_time < timeout:
            result = self._run_oc(
                "get", "pod", pod_name,
                "-n", ns,
                "-o", "jsonpath={.status.phase}",
                check=False,
            )

            if result.returncode == 0:
                phase = result.stdout.strip()
                if phase == "Running":
                    return
                elif phase in ("Failed", "Error"):
                    # Get pod events for debugging
                    events = self._run_oc(
                        "get", "events",
                        "-n", ns,
                        "--field-selector", f"involvedObject.name={pod_name}",
                        "-o", "jsonpath={.items[-1].message}",
                        check=False,
                    )
                    msg = events.stdout.strip() if events.returncode == 0 else ""
                    raise PodNotReadyError(f"Pod {pod_name} failed: {phase}. {msg}")

            time.sleep(2)

        raise PodNotReadyError(
            f"Pod {pod_name} not ready within {timeout} seconds"
        )

    def _generate_statefulset_spec(
        self,
        session_name: str,
        image: str,
        env: dict[str, str],
        workspace: Path,
        pvc_size: str = "10Gi",
        storage_class: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Kubernetes StatefulSet specification for persistent sessions.

        Credentials (gcloud, claude, gitconfig) are synced to /pvc/config via
        _sync_config_to_pod() after the pod starts, not mounted as Secrets.

        Args:
            session_name: Session name.
            image: Container image to use.
            env: Environment variables to set.
            workspace: Local workspace path (for annotation).
            pvc_size: Size of the PVC (e.g., "10Gi").
            storage_class: Storage class name (None for default).

        Returns:
            StatefulSet spec as a dictionary.
        """
        sts_name = f"paude-{session_name}"
        created_at = datetime.now(UTC).isoformat()

        # Convert env dict to list of name/value dicts
        env_list = [{"name": k, "value": v} for k, v in env.items()]

        # PVC mounted at /pvc - credentials and workspace synced there
        volume_mounts: list[dict[str, Any]] = [
            {
                "name": "workspace",
                "mountPath": "/pvc",
            },
        ]

        # No additional volumes - credentials are synced via oc cp to /pvc/config
        volumes: list[dict[str, Any]] = []

        # Build PVC spec for volumeClaimTemplates
        pvc_spec: dict[str, Any] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {
                "requests": {
                    "storage": pvc_size,
                },
            },
        }
        if storage_class:
            pvc_spec["storageClassName"] = storage_class

        sts_spec: dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {
                "name": sts_name,
                "namespace": self.namespace,
                "labels": {
                    "app": "paude",
                    "paude.io/session-name": session_name,
                },
                "annotations": {
                    "paude.io/workspace": _encode_path(workspace),
                    "paude.io/created-at": created_at,
                },
            },
            "spec": {
                "replicas": 0,  # Start stopped
                "serviceName": sts_name,
                "selector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": session_name,
                    },
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "paude",
                            "paude.io/session-name": session_name,
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "paude",
                                "image": image,
                                "imagePullPolicy": "Always",
                                "command": ["/usr/local/bin/entrypoint-session.sh"],
                                "stdin": True,
                                "tty": True,
                                "env": env_list + [{
                                    "name": "PAUDE_WORKSPACE",
                                    "value": "/pvc/workspace",
                                }],
                                # Don't set workingDir - kubelet creates it as root
                                # Entrypoint creates dir with correct UID and cd's to it
                                "resources": self._config.resources,
                                "volumeMounts": volume_mounts,
                            },
                        ],
                        "volumes": volumes,
                        "restartPolicy": "Always",
                    },
                },
                "volumeClaimTemplates": [
                    {
                        "metadata": {
                            "name": "workspace",
                        },
                        "spec": pvc_spec,
                    },
                ],
            },
        }

        return sts_spec

    def _get_statefulset(self, session_name: str) -> dict[str, Any] | None:
        """Get StatefulSet for a session.

        Args:
            session_name: Session name.

        Returns:
            StatefulSet data or None if not found.
        """
        sts_name = f"paude-{session_name}"
        ns = self.namespace

        result = self._run_oc(
            "get", "statefulset", sts_name,
            "-n", ns,
            "-o", "json",
            check=False,
        )

        if result.returncode != 0:
            return None

        try:
            data: dict[str, Any] = json.loads(result.stdout)
            return data
        except json.JSONDecodeError:
            return None

    def _get_pod_for_session(self, session_name: str) -> str | None:
        """Get the pod name for a session.

        For StatefulSets, the pod name is predictable: {sts-name}-0.

        Args:
            session_name: Session name.

        Returns:
            Pod name or None if not found/not running.
        """
        pod_name = f"paude-{session_name}-0"
        ns = self.namespace

        result = self._run_oc(
            "get", "pod", pod_name,
            "-n", ns,
            "-o", "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            return None

        return pod_name

    def _scale_statefulset(self, session_name: str, replicas: int) -> None:
        """Scale a StatefulSet to the specified number of replicas.

        Args:
            session_name: Session name.
            replicas: Number of replicas (0 or 1).
        """
        sts_name = f"paude-{session_name}"
        ns = self.namespace

        self._run_oc(
            "scale", "statefulset", sts_name,
            "-n", ns,
            f"--replicas={replicas}",
        )

    def _scale_deployment(self, deployment_name: str, replicas: int) -> None:
        """Scale a Deployment to the specified number of replicas.

        Args:
            deployment_name: Deployment name.
            replicas: Number of replicas (0 or 1).
        """
        ns = self.namespace
        self._run_oc(
            "scale", "deployment", deployment_name,
            "-n", ns,
            f"--replicas={replicas}",
            check=False,  # Don't fail if deployment doesn't exist
        )

    # -------------------------------------------------------------------------
    # New Backend Protocol Methods (persistent sessions)
    # -------------------------------------------------------------------------

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new persistent session (does not start it).

        Creates StatefulSet + credentials + NetworkPolicy with replicas=0.

        Args:
            config: Session configuration.

        Returns:
            Session object representing the created session.
        """
        # Check connection
        self._check_connection()

        # Verify namespace exists
        self._verify_namespace()

        # Generate or use provided session name
        session_name = config.name or _generate_session_name(config.workspace)

        # Check if session already exists
        if self._get_statefulset(session_name) is not None:
            raise SessionExistsError(f"Session '{session_name}' already exists")

        ns = self.namespace
        created_at = datetime.now(UTC).isoformat()

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        # Apply network policy based on config
        if config.network_restricted:
            # Create proxy pod and service first (before NetworkPolicy)
            # Derive proxy image from the main image
            proxy_image = config.image.replace(
                "paude-base-centos9", "paude-proxy-centos9"
            )
            # If image doesn't contain the expected pattern, use a default
            if proxy_image == config.image:
                proxy_image = "quay.io/bbrowning/paude-proxy-centos9:latest"

            self._create_proxy_deployment(session_name, proxy_image)
            self._create_proxy_service(session_name)

            # Create NetworkPolicy for proxy (allows all egress for squid)
            self._ensure_proxy_network_policy(session_name)

            # Now create NetworkPolicy that allows traffic to the proxy
            self._ensure_network_policy(session_name)
        else:
            self._ensure_network_policy_permissive(session_name)

        # Build environment variables
        session_env = dict(config.env)
        claude_args = list(config.args)
        if config.yolo:
            claude_args = ["--dangerously-skip-permissions"] + claude_args
        if claude_args:
            session_env["PAUDE_CLAUDE_ARGS"] = " ".join(claude_args)

        # Add proxy environment variables when network is restricted
        if config.network_restricted:
            proxy_url = f"http://paude-proxy-{session_name}:3128"
            session_env["HTTP_PROXY"] = proxy_url
            session_env["HTTPS_PROXY"] = proxy_url
            session_env["http_proxy"] = proxy_url
            session_env["https_proxy"] = proxy_url

        # Generate and apply StatefulSet spec
        # Credentials are synced to /pvc/config when session starts, not via Secrets
        sts_spec = self._generate_statefulset_spec(
            session_name=session_name,
            image=config.image,
            env=session_env,
            workspace=config.workspace,
            pvc_size=config.pvc_size,
            storage_class=config.storage_class,
        )

        print(f"Creating StatefulSet/paude-{session_name} in namespace {ns}...",
              file=sys.stderr)
        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(sts_spec),
        )

        print(f"Session '{session_name}' created (stopped).", file=sys.stderr)

        return Session(
            name=session_name,
            status="stopped",
            workspace=config.workspace,
            created_at=created_at,
            backend_type="openshift",
            container_id=f"paude-{session_name}-0",
            volume_name=f"workspace-paude-{session_name}-0",
        )

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources.

        Args:
            name: Session name.
            confirm: Whether the user has confirmed deletion.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If confirm=False.
        """
        if not confirm:
            raise ValueError("Deletion requires confirmation. Use --confirm flag.")

        # Check if session exists
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")

        ns = self.namespace
        sts_name = f"paude-{name}"
        pvc_name = f"workspace-{sts_name}-0"

        print(f"Deleting session '{name}'...", file=sys.stderr)

        # Scale to 0 first to gracefully stop pod
        print(f"Scaling StatefulSet/{sts_name} to 0...", file=sys.stderr)
        self._run_oc(
            "scale", "statefulset", sts_name,
            "-n", ns,
            "--replicas=0",
            check=False,
        )

        # Delete StatefulSet
        print(f"Deleting StatefulSet/{sts_name}...", file=sys.stderr)
        self._run_oc(
            "delete", "statefulset", sts_name,
            "-n", ns,
            "--grace-period=0",
            check=False,
        )

        # Delete PVC (volumeClaimTemplates don't delete PVCs automatically)
        print(f"Deleting PVC/{pvc_name}...", file=sys.stderr)
        self._run_oc(
            "delete", "pvc", pvc_name,
            "-n", ns,
            check=False,
        )

        # Delete session-specific NetworkPolicies
        print("Deleting NetworkPolicy for session...", file=sys.stderr)
        self._run_oc(
            "delete", "networkpolicy",
            "-n", ns,
            "-l", f"paude.io/session-name={name}",
            check=False,
        )

        # Delete proxy Deployment and Service (if they exist)
        self._delete_proxy_resources(name)

        # Delete Build objects created for this session
        self._delete_session_builds(name)

        print(f"Session '{name}' deleted.", file=sys.stderr)

    def _sync_config_to_pod(
        self,
        pod_name: str,
        verbose: bool = False,
    ) -> None:
        """Sync credentials to pod /pvc/config/ directory.

        Creates the config directory structure and syncs:
        - gcloud credentials (ADC, credentials.db, access_tokens.db)
        - Claude config files (settings.json, credentials.json, etc.)
        - gitconfig
        - global gitignore (~/.config/git/ignore)

        Uses mkdir -p (idempotent) with chmod g+rwX for OpenShift arbitrary UID.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
        """
        ns = self.namespace
        home = Path.home()
        config_path = "/pvc/config"

        print("Syncing configuration to pod...", file=sys.stderr)

        # Prepare config directory with OpenShift UID pattern
        # Using mkdir -p (idempotent) instead of rm -rf to preserve working directories
        # Chmod may fail if different UID owns it, but g+rwX permissions are already set
        prep_result = self._run_oc(
            "exec", pod_name, "-n", ns, "--",
            "bash", "-c",
            f"mkdir -p {config_path}/gcloud {config_path}/claude && "
            f"(chmod -R g+rwX {config_path} 2>/dev/null || true)",
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )
        if prep_result.returncode != 0:
            raise OpenShiftError(
                f"Failed to prepare config directory: {prep_result.stderr}"
            )

        # Sync gcloud credentials
        gcloud_dir = home / ".config" / "gcloud"
        gcloud_files = [
            "application_default_credentials.json",
            "credentials.db",
            "access_tokens.db",
        ]
        gcloud_dest = f"{pod_name}:{config_path}/gcloud"
        for filename in gcloud_files:
            filepath = gcloud_dir / filename
            if filepath.exists():
                try:
                    self._run_oc(
                        "cp", str(filepath), f"{gcloud_dest}/{filename}",
                        "-n", ns, check=False,
                    )
                except Exception:  # noqa: S110
                    pass  # Skip unreadable files

        # Sync entire ~/.claude/ directory (not individual files)
        claude_dir = home / ".claude"
        claude_json = home / ".claude.json"

        if claude_dir.is_dir():
            # Build exclude args for session-specific files
            exclude_args = []
            for pattern in CLAUDE_EXCLUDES:
                exclude_args.extend(["--exclude", pattern])

            # Rsync ~/.claude/ to /pvc/config/claude/
            rsync_success = self._rsync_with_retry(
                f"{claude_dir}/",
                f"{pod_name}:{config_path}/claude",
                ns,
                exclude_args,
                verbose=verbose,
            )

            # Rewrite absolute paths in plugin metadata (only if rsync succeeded)
            if rsync_success:
                self._rewrite_plugin_paths(pod_name, config_path)
                if verbose:
                    print("  Synced ~/.claude/ (including plugins)", file=sys.stderr)
            else:
                print(
                    "  Warning: Failed to sync ~/.claude/ - plugins may not work",
                    file=sys.stderr,
                )

        # .claude.json (main config) goes to claude/claude.json
        if claude_json.exists():
            try:
                dest = f"{pod_name}:{config_path}/claude/claude.json"
                self._run_oc(
                    "cp", str(claude_json), dest, "-n", ns, check=False,
                )
            except Exception:  # noqa: S110
                pass

        # Sync gitconfig
        gitconfig = home / ".gitconfig"
        if gitconfig.exists():
            try:
                self._run_oc(
                    "cp", str(gitconfig), f"{pod_name}:{config_path}/gitconfig",
                    "-n", ns, check=False,
                )
                if verbose:
                    print("  Synced ~/.gitconfig", file=sys.stderr)
            except Exception:  # noqa: S110
                pass

        # Sync global gitignore if it exists
        global_gitignore = home / ".config" / "git" / "ignore"
        if global_gitignore.exists():
            try:
                self._run_oc(
                    "cp", str(global_gitignore),
                    f"{pod_name}:{config_path}/gitignore-global",
                    "-n", ns, check=False,
                )
                if verbose:
                    print("  Synced ~/.config/git/ignore (global gitignore)",
                          file=sys.stderr)
            except Exception:  # noqa: S110
                pass

        # Make synced files read-only (but group-readable for OpenShift)
        # and create .ready marker
        self._run_oc(
            "exec", pod_name, "-n", ns, "--",
            "bash", "-c",
            f"chmod -R g+rX {config_path} && "
            f"touch {config_path}/.ready && "
            f"chmod g+r {config_path}/.ready",
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )

        print("Configuration synced.", file=sys.stderr)

    def _rewrite_plugin_paths(self, pod_name: str, config_path: str) -> None:
        """Rewrite absolute paths in plugin metadata files using jq.

        Claude Code writes plugin paths as absolute host paths. These need
        to be rewritten to container paths (/home/paude/.claude/plugins/...).

        Uses jq for field-specific rewriting to avoid accidental replacements.
        """
        ns = self.namespace
        container_plugins_path = "/home/paude/.claude/plugins"

        # Rewrite installed_plugins.json - installPath field
        # Structure: { "plugins": { "<name>": [{ "installPath": "..." }] } }
        installed_plugins = f"{config_path}/claude/plugins/installed_plugins.json"
        # jq expression: extract last 3 path components and prepend container path
        # Uses select() to skip entries where installPath is null/missing
        jq_expr = (
            '.plugins |= with_entries(.value |= map('
            'if .installPath then '
            '.installPath = ($prefix + "/" + '
            '(.installPath | split("/") | .[-3:] | join("/"))) '
            'else . end))'
        )
        self._run_oc(
            "exec", pod_name, "-n", ns, "--",
            "bash", "-c",
            f'if [ -f "{installed_plugins}" ]; then '
            f'jq --arg prefix "{container_plugins_path}/cache" \'{jq_expr}\' '
            f'"{installed_plugins}" > "{installed_plugins}.tmp" && '
            f'mv "{installed_plugins}.tmp" "{installed_plugins}"; fi',
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )

        # Rewrite known_marketplaces.json - installLocation field
        # Structure: { "<name>": { "installLocation": "..." } }
        known_marketplaces = f"{config_path}/claude/plugins/known_marketplaces.json"
        # Only rewrite if .value has installLocation field
        jq_expr2 = (
            'with_entries(if .value.installLocation then '
            '.value.installLocation = ($prefix + "/marketplaces/" + .key) '
            'else . end)'
        )
        self._run_oc(
            "exec", pod_name, "-n", ns, "--",
            "bash", "-c",
            f'if [ -f "{known_marketplaces}" ]; then '
            f'jq --arg prefix "{container_plugins_path}" \'{jq_expr2}\' '
            f'"{known_marketplaces}" > "{known_marketplaces}.tmp" && '
            f'mv "{known_marketplaces}.tmp" "{known_marketplaces}"; fi',
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )

    def start_session(
        self,
        name: str,
        sync: bool = True,
        verbose: bool = False,
    ) -> int:
        """Start a session and connect to it.

        Scales StatefulSet to 1, syncs files, connects.

        Args:
            name: Session name.
            sync: Whether to sync workspace files before connecting.
            verbose: Whether to show rsync output (default False).

        Returns:
            Exit code from the connected session.
        """
        # Check if session exists
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")

        pod_name = f"paude-{name}-0"

        # Scale to 1
        print(f"Starting session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 1)

        # Wait for proxy to be ready (if it exists)
        # Check if proxy deployment exists for this session
        proxy_deployment = f"paude-proxy-{name}"
        result = self._run_oc(
            "get", "deployment", proxy_deployment,
            "-n", self.namespace,
            check=False,
        )
        if result.returncode == 0:
            # Proxy exists, scale it up and wait for ready
            self._scale_deployment(proxy_deployment, 1)
            self._wait_for_proxy_ready(name)

        # Wait for pod to be ready
        print(f"Waiting for Pod/{pod_name} to be ready...", file=sys.stderr)
        try:
            self._wait_for_pod_ready(pod_name)
        except PodNotReadyError as e:
            print(f"Pod failed to start: {e}", file=sys.stderr)
            return 1

        # Sync credentials to pod (gcloud, claude config, gitconfig)
        self._sync_config_to_pod(pod_name, verbose=verbose)

        # Sync workspace if requested
        if sync:
            print("Syncing workspace to pod...", file=sys.stderr)
            self.sync_session(name, direction="remote", verbose=verbose)

        # Connect to session
        return self.connect_session(name)

    def stop_session(
        self,
        name: str,
        sync: bool = False,
        verbose: bool = False,
    ) -> None:
        """Stop a session (preserves volume).

        Scales StatefulSet to 0 but keeps PVC intact.

        Args:
            name: Session name.
            sync: Whether to sync files back to local before stopping.
            verbose: Whether to show rsync output (default False).
        """
        # Check if session exists
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")

        # Sync if requested
        if sync:
            print("Syncing workspace from pod...", file=sys.stderr)
            try:
                self.sync_session(name, direction="local", verbose=verbose)
            except Exception as e:
                print(f"Warning: Sync failed: {e}", file=sys.stderr)

        # Scale to 0
        print(f"Stopping session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 0)

        # Scale proxy to 0 if it exists
        proxy_deployment = f"paude-proxy-{name}"
        result = self._run_oc(
            "get", "deployment", proxy_deployment,
            "-n", self.namespace,
            check=False,
        )
        if result.returncode == 0:
            print(f"Stopping proxy '{proxy_deployment}'...", file=sys.stderr)
            self._scale_deployment(proxy_deployment, 0)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(self, name: str) -> int:
        """Attach to a running session.

        Args:
            name: Session name.

        Returns:
            Exit code from the attached session.
        """
        pod_name = self._get_pod_for_session(name)
        if pod_name is None:
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return 1

        ns = self.namespace

        # Verify pod is running
        result = self._run_oc(
            "get", "pod", pod_name,
            "-n", ns,
            "-o", "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0 or result.stdout.strip() != "Running":
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return 1

        # Attach using oc exec with interactive TTY
        exec_cmd = ["oc", "exec", "-it", "-n", ns, pod_name, "--"]

        if self._config.context:
            exec_cmd = [
                "oc", "--context", self._config.context,
                "exec", "-it", "-n", ns, pod_name, "--",
            ]

        # Use session entrypoint for session persistence
        exec_cmd.append("/usr/local/bin/entrypoint-session.sh")

        exec_result = subprocess.run(exec_cmd)

        # Reset terminal state after tmux disconnection
        os.system("stty sane 2>/dev/null")  # noqa: S605

        return exec_result.returncode

    def get_session(self, name: str) -> Session | None:
        """Get a session by name.

        Args:
            name: Session name.

        Returns:
            Session object or None if not found.
        """
        sts = self._get_statefulset(name)
        if sts is None:
            return None

        metadata = sts.get("metadata", {})
        annotations = metadata.get("annotations", {})
        spec = sts.get("spec", {})

        # Determine status from replicas
        replicas = spec.get("replicas", 0)
        status_replicas = sts.get("status", {}).get("readyReplicas", 0)

        if replicas == 0:
            status = "stopped"
        elif status_replicas > 0:
            status = "running"
        else:
            status = "pending"

        # Decode workspace
        workspace_encoded = annotations.get("paude.io/workspace", "")
        try:
            workspace = (
                _decode_path(workspace_encoded)
                if workspace_encoded
                else Path("/workspace")
            )
        except Exception:
            workspace = Path("/workspace")

        return Session(
            name=name,
            status=status,
            workspace=workspace,
            created_at=annotations.get("paude.io/created-at", ""),
            backend_type="openshift",
            container_id=f"paude-{name}-0",
            volume_name=f"workspace-paude-{name}-0",
        )

    def sync_session(
        self,
        name: str,
        direction: str = "both",
        verbose: bool = False,
    ) -> None:
        """Sync files between local and remote workspace.

        Args:
            name: Session name.
            direction: Sync direction ("local", "remote", "both").
            verbose: Whether to show rsync output (default False).
        """
        # Get session to find workspace
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")

        # Get workspace from annotations
        annotations = sts.get("metadata", {}).get("annotations", {})
        workspace_encoded = annotations.get("paude.io/workspace")
        if not workspace_encoded:
            print("No workspace path found in session.", file=sys.stderr)
            return

        workspace = _decode_path(workspace_encoded)
        remote_path = "/pvc/workspace"

        # StatefulSet pod name is paude-{session-name}-0
        pod_name = f"paude-{name}-0"
        ns = self.namespace

        # Verify pod is running
        result = self._run_oc(
            "get", "pod", pod_name,
            "-n", ns,
            "-o", "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            print(f"Session '{name}' pod not found. Is it running?", file=sys.stderr)
            return

        phase = result.stdout.strip()
        if phase != "Running":
            print(
                f"Session '{name}' is not running (status: {phase}).",
                file=sys.stderr,
            )
            return

        # Default excludes - exclude venvs and build artifacts, but NOT .git
        excludes = [
            ".venv",
            "venv",
            ".virtualenv",
            "env",
            ".env",
            "__pycache__",
            "*.pyc",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".tox",
            ".nox",
            ".coverage",
            "htmlcov",
            "*.egg-info",
            "dist",
            "build",
            "node_modules",
        ]

        exclude_args: list[str] = []
        for pattern in excludes:
            exclude_args.extend(["--exclude", pattern])

        # Sync based on direction
        if direction in ("remote", "both"):
            # Prepare workspace directory with proper permissions
            # Use mkdir -p (idempotent) instead of rm -rf to preserve CWD
            # g+rwX allows any pod (all run as GID 0) to access the directory
            # Chmod may fail if different UID owns it, but perms already set
            prep_result = self._run_oc(
                "exec", pod_name, "-n", ns, "--",
                "bash", "-c",
                f"mkdir -p {remote_path} && "
                f"(chmod g+rwX {remote_path} 2>/dev/null || true)",
                check=False,
                timeout=self.OC_EXEC_TIMEOUT,
            )
            if prep_result.returncode != 0:
                print(
                    f"Warning: workspace prep failed: {prep_result.stderr}",
                    file=sys.stderr,
                )
            # Local to remote with --delete for incremental cleanup
            print(f"Syncing local → {pod_name}:{remote_path}...", file=sys.stderr)
            success = self._rsync_with_retry(
                f"{workspace}/",
                f"{pod_name}:{remote_path}",
                ns,
                exclude_args,
                verbose=verbose,
                delete=True,
            )
            if success:
                # Make synced subdirectories group-writable for OpenShift arbitrary UID
                # rsync/tar creates directories with restrictive permissions
                self._run_oc(
                    "exec", pod_name, "-n", ns, "--",
                    "chmod", "-R", "g+rwX", remote_path,
                    check=False,
                    timeout=self.OC_EXEC_TIMEOUT,
                )
                print("Sync to remote completed.", file=sys.stderr)
            else:
                print("Sync to remote FAILED.", file=sys.stderr)

        if direction in ("local", "both"):
            # Remote to local
            print(f"Syncing {pod_name}:{remote_path} → local...", file=sys.stderr)
            success = self._rsync_with_retry(
                f"{pod_name}:{remote_path}/",
                str(workspace),
                ns,
                exclude_args,
                verbose=verbose,
            )
            if success:
                print("Sync to local completed.", file=sys.stderr)
            else:
                print("Sync to local FAILED.", file=sys.stderr)

    def find_session_for_workspace(self, workspace: Path) -> Session | None:
        """Find an existing session for the given workspace.

        Args:
            workspace: Workspace path to search for.

        Returns:
            Session if found, None otherwise.
        """
        sessions = self.list_sessions()
        workspace_resolved = workspace.resolve()

        for session in sessions:
            if session.workspace.resolve() == workspace_resolved:
                return session

        return None

    def list_sessions(self) -> list[Session]:
        """List all sessions (StatefulSets).

        Returns:
            List of Session objects.
        """
        ns = self.namespace
        sessions = []

        result = self._run_oc(
            "get", "statefulsets",
            "-n", ns,
            "-l", "app=paude",
            "-o", "json",
            check=False,
        )

        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                for item in data.get("items", []):
                    metadata = item.get("metadata", {})
                    labels = metadata.get("labels", {})
                    annotations = metadata.get("annotations", {})
                    spec = item.get("spec", {})
                    sts_status = item.get("status", {})

                    session_name = labels.get("paude.io/session-name", "unknown")

                    # Determine status from replicas
                    replicas = spec.get("replicas", 0)
                    ready_replicas = sts_status.get("readyReplicas", 0)

                    if replicas == 0:
                        status = "stopped"
                    elif ready_replicas > 0:
                        status = "running"
                    else:
                        status = "pending"

                    # Decode workspace path
                    workspace_encoded = annotations.get("paude.io/workspace", "")
                    try:
                        workspace = (
                            _decode_path(workspace_encoded)
                            if workspace_encoded
                            else Path("/workspace")
                        )
                    except Exception:
                        workspace = Path("/workspace")

                    created_at = annotations.get(
                        "paude.io/created-at", metadata.get("creationTimestamp", "")
                    )
                    sessions.append(Session(
                        name=session_name,
                        status=status,
                        workspace=workspace,
                        created_at=created_at,
                        backend_type="openshift",
                        container_id=f"paude-{session_name}-0",
                        volume_name=f"workspace-paude-{session_name}-0",
                    ))
            except json.JSONDecodeError:
                pass

        return sessions
