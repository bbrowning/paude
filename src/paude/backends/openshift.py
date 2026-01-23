"""OpenShift backend implementation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.base import Session, SessionConfig


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
    ) -> bool:
        """Run oc rsync with retry logic for timeouts.

        Args:
            source: Source path (local or pod:path format).
            dest: Destination path (local or pod:path format).
            namespace: Kubernetes namespace.
            exclude_args: List of --exclude arguments.
            verbose: Whether to show rsync output (default False).

        Returns:
            True if sync succeeded, False if all retries failed.
        """
        for attempt in range(1, self.RSYNC_MAX_RETRIES + 1):
            try:
                # Show output only when verbose is enabled
                self._run_oc(
                    "rsync",
                    "--progress",
                    source,
                    dest,
                    "-n", namespace,
                    "--no-perms",
                    *exclude_args,
                    timeout=self.RSYNC_TIMEOUT,
                    capture=not verbose,
                    check=False,
                )
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
    ) -> str:
        """Start a binary build and return the build name.

        Args:
            config_hash: Hash of the configuration for naming.
            context_dir: Path to the build context directory.

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

            build_name = self._start_binary_build(config_hash, build_ctx.context_dir)

            self._wait_for_build(build_name)

            return self._get_imagestream_reference(config_hash)

        finally:
            shutil.rmtree(build_ctx.context_dir, ignore_errors=True)

    def _ensure_network_policy(self, session_id: str) -> None:
        """Ensure a NetworkPolicy exists that restricts egress traffic for this session.

        Creates a NetworkPolicy that:
        - Allows egress to Google Cloud APIs (Vertex AI)
        - Allows egress to Anthropic API
        - Allows egress to DNS (UDP 53)
        - Denies all other egress traffic

        The policy applies only to the pod for this specific session.

        Args:
            session_id: The session ID to scope the policy to.
        """
        ns = self.namespace
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace {ns}...",
            file=sys.stderr,
        )

        # Define allowed CIDR blocks
        # Note: These are approximate ranges - production should use DNS-based policies
        # or a more sophisticated egress controller
        allowed_cidrs = [
            # Google Cloud APIs (googleapis.com)
            "142.250.0.0/16",
            "172.217.0.0/16",
            "216.58.0.0/16",
            "172.253.0.0/16",
            "74.125.0.0/16",
            # Anthropic API (api.anthropic.com)
            "104.18.0.0/16",
            "172.66.0.0/16",
        ]

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
                    # Allow DNS
                    {
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                        ],
                    },
                    # Allow HTTPS to specific CIDRs
                    {
                        "ports": [
                            {"protocol": "TCP", "port": 443},
                        ],
                        "to": [
                            {"ipBlock": {"cidr": cidr}}
                            for cidr in allowed_cidrs
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

    def _create_credentials_secret(
        self,
        session_id: str,
    ) -> str | None:
        """Create a secret containing gcloud credentials.

        Args:
            session_id: Session ID for labeling.

        Returns:
            Secret name if created, None if no credentials found.
        """
        home = Path.home()
        gcloud_dir = home / ".config" / "gcloud"

        if not gcloud_dir.exists():
            print("No gcloud credentials found, skipping.", file=sys.stderr)
            return None

        ns = self.namespace
        secret_name = f"paude-gcloud-{session_id}"

        # Create secret from directory contents
        # Only include essential credential files
        files_to_include = [
            "application_default_credentials.json",
            "credentials.db",
            "access_tokens.db",
        ]

        # Build secret data from files
        secret_data: dict[str, str] = {}
        import base64
        for filename in files_to_include:
            filepath = gcloud_dir / filename
            if filepath.exists():
                try:
                    content = filepath.read_bytes()
                    secret_data[filename] = base64.b64encode(content).decode()
                except OSError:
                    continue  # Skip unreadable files

        if not secret_data:
            print("No gcloud credential files found.", file=sys.stderr)
            return None

        secret_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": ns,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "type": "Opaque",
            "data": secret_data,
        }

        print(f"Creating Secret/{secret_name} in namespace {ns}...", file=sys.stderr)
        self._run_oc("apply", "-f", "-", input_data=json.dumps(secret_spec))

        return secret_name

    def _create_gitconfig_configmap(
        self,
        session_id: str,
    ) -> str | None:
        """Create a ConfigMap containing .gitconfig.

        Args:
            session_id: Session ID for labeling.

        Returns:
            ConfigMap name if created, None if no config found.
        """
        home = Path.home()
        gitconfig = home / ".gitconfig"

        if not gitconfig.exists():
            return None

        ns = self.namespace
        cm_name = f"paude-gitconfig-{session_id}"

        try:
            content = gitconfig.read_text()
        except Exception:
            return None

        cm_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": cm_name,
                "namespace": ns,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "data": {
                ".gitconfig": content,
            },
        }

        print(f"Creating ConfigMap/{cm_name} in namespace {ns}...", file=sys.stderr)
        self._run_oc("apply", "-f", "-", input_data=json.dumps(cm_spec))

        return cm_name

    def _create_claude_secret(
        self,
        session_id: str,
    ) -> str | None:
        """Create a secret containing Claude configuration.

        Only includes essential config files, not logs/cache/history.

        Args:
            session_id: Session ID for labeling.

        Returns:
            Secret name if created, None if no config found.
        """
        home = Path.home()
        claude_dir = home / ".claude"
        claude_json = home / ".claude.json"

        if not claude_dir.exists() and not claude_json.exists():
            return None

        ns = self.namespace
        secret_name = f"paude-claude-{session_id}"

        import base64
        secret_data: dict[str, str] = {}

        # Only include specific essential files from .claude directory
        # Exclude: logs, databases, projects, todos, cache
        essential_files = [
            "settings.json",
            "credentials.json",
            "statsig.json",
        ]

        # Include .claude.json if it exists (main config file)
        if claude_json.exists():
            try:
                content = claude_json.read_bytes()
                secret_data["claude.json"] = base64.b64encode(content).decode()
            except OSError:
                pass  # File unreadable, skip it

        # Include only essential files from .claude directory
        if claude_dir.exists():
            for filename in essential_files:
                filepath = claude_dir / filename
                if filepath.exists() and filepath.is_file():
                    try:
                        content = filepath.read_bytes()
                        secret_data[filename] = base64.b64encode(content).decode()
                    except OSError:
                        continue  # Skip unreadable files

        if not secret_data:
            return None

        secret_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": secret_name,
                "namespace": ns,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "type": "Opaque",
            "data": secret_data,
        }

        print(f"Creating Secret/{secret_name} in namespace {ns}...", file=sys.stderr)
        self._run_oc("apply", "-f", "-", input_data=json.dumps(secret_spec))

        return secret_name

    def _generate_session_id(self) -> str:
        """Generate a unique session ID.

        Returns:
            Session ID string.
        """
        return f"{int(time.time())}-{secrets.token_hex(4)}"

    def _generate_pod_spec(
        self,
        session_id: str,
        image: str,
        env: dict[str, str],
        gcloud_secret: str | None = None,
        gitconfig_cm: str | None = None,
        claude_secret: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Kubernetes Pod specification.

        Args:
            session_id: Unique session identifier.
            image: Container image to use.
            env: Environment variables to set.
            gcloud_secret: Name of gcloud credentials secret.
            gitconfig_cm: Name of gitconfig ConfigMap.
            claude_secret: Name of Claude config secret.

        Returns:
            Pod spec as a dictionary.
        """
        pod_name = f"paude-session-{session_id}"

        # Convert env dict to list of name/value dicts
        env_list = [{"name": k, "value": v} for k, v in env.items()]

        # Build volume mounts
        volume_mounts: list[dict[str, Any]] = [
            {
                "name": "workspace",
                "mountPath": "/workspace",
            },
        ]

        # Build volumes
        volumes: list[dict[str, Any]] = [
            {
                "name": "workspace",
                "emptyDir": {},
            },
        ]

        # Add gcloud credentials if available
        if gcloud_secret:
            volume_mounts.append({
                "name": "gcloud-creds",
                "mountPath": "/home/paude/.config/gcloud",
                "readOnly": True,
            })
            volumes.append({
                "name": "gcloud-creds",
                "secret": {
                    "secretName": gcloud_secret,
                },
            })

        # Add gitconfig if available
        if gitconfig_cm:
            volume_mounts.append({
                "name": "gitconfig",
                "mountPath": "/home/paude/.gitconfig",
                "subPath": ".gitconfig",
                "readOnly": True,
            })
            volumes.append({
                "name": "gitconfig",
                "configMap": {
                    "name": gitconfig_cm,
                },
            })

        # Add claude config if available
        if claude_secret:
            volume_mounts.append({
                "name": "claude-config",
                "mountPath": "/tmp/claude.seed",  # noqa: S108
                "readOnly": True,
            })
            volumes.append({
                "name": "claude-config",
                "secret": {
                    "secretName": claude_secret,
                },
            })

        pod_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name,
                "namespace": self.namespace,
                "labels": {
                    "app": "paude",
                    "role": "workload",
                    "session-id": session_id,
                },
            },
            "spec": {
                "containers": [
                    {
                        "name": "paude",
                        "image": image,
                        "imagePullPolicy": "Always",
                        "command": ["/usr/local/bin/entrypoint.sh"],
                        "stdin": True,
                        "tty": True,
                        "env": env_list,
                        "workingDir": "/workspace",
                        "resources": self._config.resources,
                        "volumeMounts": volume_mounts,
                    },
                ],
                "volumes": volumes,
                "restartPolicy": "Never",
            },
        }

        return pod_spec

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
        gcloud_secret: str | None = None,
        gitconfig_cm: str | None = None,
        claude_secret: str | None = None,
        pvc_size: str = "10Gi",
        storage_class: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Kubernetes StatefulSet specification for persistent sessions.

        Args:
            session_name: Session name.
            image: Container image to use.
            env: Environment variables to set.
            workspace: Local workspace path (for annotation).
            gcloud_secret: Name of gcloud credentials secret.
            gitconfig_cm: Name of gitconfig ConfigMap.
            claude_secret: Name of Claude config secret.
            pvc_size: Size of the PVC (e.g., "10Gi").
            storage_class: Storage class name (None for default).

        Returns:
            StatefulSet spec as a dictionary.
        """
        sts_name = f"paude-{session_name}"
        created_at = datetime.now(UTC).isoformat()

        # Convert env dict to list of name/value dicts
        env_list = [{"name": k, "value": v} for k, v in env.items()]

        # Build volume mounts - PVC mounted at /pvc
        volume_mounts: list[dict[str, Any]] = [
            {
                "name": "workspace",
                "mountPath": "/pvc",
            },
        ]

        # Build non-PVC volumes
        volumes: list[dict[str, Any]] = []

        # Add gcloud credentials if available
        if gcloud_secret:
            volume_mounts.append({
                "name": "gcloud-creds",
                "mountPath": "/home/paude/.config/gcloud",
                "readOnly": True,
            })
            volumes.append({
                "name": "gcloud-creds",
                "secret": {
                    "secretName": gcloud_secret,
                },
            })

        # Add gitconfig if available
        if gitconfig_cm:
            volume_mounts.append({
                "name": "gitconfig",
                "mountPath": "/home/paude/.gitconfig",
                "subPath": ".gitconfig",
                "readOnly": True,
            })
            volumes.append({
                "name": "gitconfig",
                "configMap": {
                    "name": gitconfig_cm,
                },
            })

        # Add claude config if available
        if claude_secret:
            volume_mounts.append({
                "name": "claude-config",
                "mountPath": "/tmp/claude.seed",  # noqa: S108
                "readOnly": True,
            })
            volumes.append({
                "name": "claude-config",
                "secret": {
                    "secretName": claude_secret,
                },
            })

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
                                "workingDir": "/pvc/workspace",
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

        # Create credential secrets and configmaps
        gcloud_secret = self._create_credentials_secret(session_name)
        gitconfig_cm = self._create_gitconfig_configmap(session_name)
        claude_secret = self._create_claude_secret(session_name)

        # Apply network policy based on config
        if config.network_restricted:
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

        # Generate and apply StatefulSet spec
        sts_spec = self._generate_statefulset_spec(
            session_name=session_name,
            image=config.image,
            env=session_env,
            workspace=config.workspace,
            gcloud_secret=gcloud_secret,
            gitconfig_cm=gitconfig_cm,
            claude_secret=claude_secret,
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

        # Delete session-specific secrets/configmaps/networkpolicies
        print("Deleting Secret,ConfigMap,NetworkPolicy for session...",
              file=sys.stderr)
        self._run_oc(
            "delete", "secret,configmap,networkpolicy",
            "-n", ns,
            "-l", f"session-id={name}",
            check=False,
        )
        # Also try with session-name label (new naming)
        self._run_oc(
            "delete", "secret,configmap,networkpolicy",
            "-n", ns,
            "-l", f"paude.io/session-name={name}",
            check=False,
        )

        print(f"Session '{name}' deleted.", file=sys.stderr)

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

        # Wait for pod to be ready
        print(f"Waiting for Pod/{pod_name} to be ready...", file=sys.stderr)
        try:
            self._wait_for_pod_ready(pod_name)
        except PodNotReadyError as e:
            print(f"Pod failed to start: {e}", file=sys.stderr)
            return 1

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
            # The PVC persists between pod restarts, but OpenShift assigns different
            # arbitrary UIDs to each pod. If /pvc/workspace was created by a previous
            # pod, we can't chmod it (EPERM). Solution: remove and recreate it.
            # This is safe because we're about to overwrite with local data anyway.
            prep_result = self._run_oc(
                "exec", pod_name, "-n", ns, "--",
                "bash", "-c",
                # Remove old workspace if it exists (might be owned by different UID)
                # Then create fresh with correct ownership and permissions
                f"rm -rf {remote_path} && "
                f"mkdir -p {remote_path} && "
                f"chmod g+rwX {remote_path}",
                check=False,
            )
            if prep_result.returncode != 0:
                print(
                    f"Warning: workspace prep failed: {prep_result.stderr}",
                    file=sys.stderr,
                )
            # Local to remote
            print(f"Syncing local → {pod_name}:{remote_path}...", file=sys.stderr)
            self._rsync_with_retry(
                f"{workspace}/",
                f"{pod_name}:{remote_path}",
                ns,
                exclude_args,
                verbose=verbose,
            )
            # Make synced subdirectories group-writable for OpenShift arbitrary UID
            # rsync/tar creates directories with restrictive permissions
            self._run_oc(
                "exec", pod_name, "-n", ns, "--",
                "chmod", "-R", "g+rwX", remote_path,
                check=False,
            )

        if direction in ("local", "both"):
            # Remote to local
            print(f"Syncing {pod_name}:{remote_path} → local...", file=sys.stderr)
            self._rsync_with_retry(
                f"{pod_name}:{remote_path}/",
                str(workspace),
                ns,
                exclude_args,
                verbose=verbose,
            )

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

    # -------------------------------------------------------------------------
    # Legacy Methods (ephemeral sessions for backward compatibility)
    # -------------------------------------------------------------------------

    def start_session_legacy(
        self,
        image: str,
        workspace: Path,
        env: dict[str, str],
        mounts: list[str],
        args: list[str],
        workdir: str | None = None,
        network_restricted: bool = True,
        yolo: bool = False,
        network: str | None = None,
    ) -> Session:
        """Start a new Claude session on OpenShift.

        Args:
            image: Container image to use.
            workspace: Local workspace path.
            env: Environment variables to set.
            mounts: Volume mount arguments (unused for OpenShift - uses sync).
            args: Arguments to pass to Claude.
            workdir: Working directory inside container.
            network_restricted: Whether to restrict network (default True).
            yolo: Enable YOLO mode (skip permission prompts).
            network: Network name (unused for OpenShift).

        Returns:
            Session object representing the started session.
        """
        # Check connection
        self._check_connection()

        # Verify namespace exists (never create)
        self._verify_namespace()

        # Generate session ID
        session_id = self._generate_session_id()
        created_at = datetime.now(UTC).isoformat()
        pod_name = f"paude-session-{session_id}"

        # Apply network policy for this session based on network_restricted flag
        if network_restricted:
            self._ensure_network_policy(session_id)
        else:
            self._ensure_network_policy_permissive(session_id)

        # Add YOLO flag to claude args if enabled
        claude_args = list(args)
        if yolo:
            claude_args = ["--dangerously-skip-permissions"] + claude_args

        # Store args in environment for entrypoint
        session_env = dict(env)
        if claude_args:
            session_env["PAUDE_CLAUDE_ARGS"] = " ".join(claude_args)

        print(f"Creating session {session_id}...", file=sys.stderr)

        # Create credential secrets and configmaps
        gcloud_secret = self._create_credentials_secret(session_id)
        gitconfig_cm = self._create_gitconfig_configmap(session_id)
        claude_secret = self._create_claude_secret(session_id)

        # Generate and apply pod spec with credentials
        pod_spec = self._generate_pod_spec(
            session_id=session_id,
            image=image,
            env=session_env,
            gcloud_secret=gcloud_secret,
            gitconfig_cm=gitconfig_cm,
            claude_secret=claude_secret,
        )

        ns = self.namespace
        print(f"Creating Pod/{pod_name} in namespace {ns}...", file=sys.stderr)
        self._run_oc(
            "apply", "-f", "-",
            input_data=json.dumps(pod_spec),
        )

        # Wait for pod to be ready
        print(f"Waiting for Pod/{pod_name} to be ready...", file=sys.stderr)
        try:
            self._wait_for_pod_ready(pod_name)
        except PodNotReadyError:
            # Get container logs before cleanup for debugging
            print("\n--- Pod failed, gathering debug info ---", file=sys.stderr)
            logs_result = self._run_oc(
                "logs", pod_name, "-n", self.namespace, check=False
            )
            if logs_result.returncode == 0 and logs_result.stdout.strip():
                print("Container logs:", file=sys.stderr)
                print(logs_result.stdout, file=sys.stderr)
            else:
                print("No container logs available.", file=sys.stderr)

            # Get recent events
            events_result = self._run_oc(
                "get", "events", "-n", self.namespace,
                "--field-selector", f"involvedObject.name={pod_name}",
                "--sort-by=.lastTimestamp",
                check=False,
            )
            if events_result.returncode == 0 and events_result.stdout.strip():
                print("\nPod events:", file=sys.stderr)
                print(events_result.stdout, file=sys.stderr)

            print("--- End debug info ---\n", file=sys.stderr)

            # Cleanup failed pod
            self._run_oc(
                "delete", "pod", pod_name, "-n", self.namespace, check=False
            )
            raise

        print(f"Session {session_id} is running.", file=sys.stderr)

        # Initial sync of workspace files to pod
        print("Syncing workspace to pod...", file=sys.stderr)
        self.sync_workspace(session_id, direction="remote", local_path=workspace)

        # Attach to the session
        exit_code = self.attach_session_legacy(session_id)

        # Determine status based on exit code
        status = "stopped" if exit_code == 0 else "error"

        return Session(
            name=session_id,
            status=status,
            workspace=workspace,
            created_at=created_at,
            backend_type="openshift",
        )

    def attach_session_legacy(self, session_id: str) -> int:
        """Attach to a running session.

        Args:
            session_id: ID of the session to attach to.

        Returns:
            Exit code from the attached session.
        """
        pod_name = f"paude-session-{session_id}"
        ns = self.namespace

        # Verify pod exists and is running
        result = self._run_oc(
            "get", "pod", pod_name,
            "-n", ns,
            "-o", "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            print(f"Session {session_id} not found.", file=sys.stderr)
            return 1

        phase = result.stdout.strip()
        if phase != "Running":
            print(
                f"Session {session_id} is not running (status: {phase}).",
                file=sys.stderr,
            )
            return 1

        # Attach using oc exec with interactive TTY
        # Uses tmux entrypoint which creates or attaches to tmux session
        exec_cmd = ["oc", "exec", "-it", "-n", ns, pod_name, "--"]

        # Add context if specified
        if self._config.context:
            exec_cmd = [
                "oc", "--context", self._config.context,
                "exec", "-it", "-n", ns, pod_name, "--",
            ]

        # Use tmux entrypoint for session persistence
        exec_cmd.append("/usr/local/bin/entrypoint.sh")

        exec_result = subprocess.run(exec_cmd)

        # Reset terminal state after tmux disconnection
        # tmux can leave terminal in bad state when connection drops
        os.system("stty sane 2>/dev/null")  # noqa: S605

        return exec_result.returncode

    def stop_session_legacy(self, session_id: str) -> None:
        """Stop and cleanup a legacy ephemeral session.

        Args:
            session_id: ID of the session to stop.
        """
        pod_name = f"paude-session-{session_id}"
        ns = self.namespace

        print(f"Deleting Pod/{pod_name} in namespace {ns}...", file=sys.stderr)

        # Delete pod
        self._run_oc(
            "delete", "pod", pod_name,
            "-n", ns,
            "--grace-period=0",
            check=False,
        )

        # Delete session-specific secrets/configmaps/networkpolicies
        print(
            f"Deleting Secret,ConfigMap,NetworkPolicy with label "
            f"session-id={session_id} in namespace {ns}...",
            file=sys.stderr,
        )
        self._run_oc(
            "delete", "secret,configmap,networkpolicy",
            "-n", ns,
            "-l", f"session-id={session_id}",
            check=False,
        )

        print(f"Session {session_id} stopped.", file=sys.stderr)

    def list_sessions_legacy(self) -> list[Session]:
        """List all sessions for current user.

        Returns:
            List of Session objects.
        """
        ns = self.namespace

        result = self._run_oc(
            "get", "pods",
            "-n", ns,
            "-l", "app=paude",
            "-o", "json",
            check=False,
        )

        if result.returncode != 0:
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        sessions = []
        for item in data.get("items", []):
            metadata = item.get("metadata", {})
            labels = metadata.get("labels", {})
            status = item.get("status", {})

            session_id = labels.get("session-id", "unknown")
            phase = status.get("phase", "Unknown")

            # Map Kubernetes phase to session status
            status_map = {
                "Running": "running",
                "Pending": "pending",
                "Succeeded": "stopped",
                "Failed": "error",
                "Unknown": "error",
            }

            sessions.append(Session(
                name=session_id,
                status=status_map.get(phase, "error"),
                workspace=Path("/workspace"),
                created_at=metadata.get("creationTimestamp", ""),
                backend_type="openshift",
            ))

        return sessions

    def list_sessions(self) -> list[Session]:
        """List all sessions (StatefulSets and legacy pods).

        Returns:
            List of Session objects.
        """
        ns = self.namespace
        sessions = []

        # First, get StatefulSet-based sessions (new session model)
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

        # Also get legacy pods (not owned by StatefulSets)
        result = self._run_oc(
            "get", "pods",
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
                    pod_status = item.get("status", {})
                    owner_refs = metadata.get("ownerReferences", [])

                    # Skip pods owned by StatefulSets (already counted above)
                    is_sts_pod = any(
                        ref.get("kind") == "StatefulSet"
                        for ref in owner_refs
                    )
                    if is_sts_pod:
                        continue

                    # This is a legacy pod
                    session_id = labels.get("session-id", "unknown")
                    phase = pod_status.get("phase", "Unknown")

                    # Map Kubernetes phase to session status
                    status_map = {
                        "Running": "running",
                        "Pending": "pending",
                        "Succeeded": "stopped",
                        "Failed": "error",
                        "Unknown": "error",
                    }

                    sessions.append(Session(
                        name=session_id,
                        status=status_map.get(phase, "error"),
                        workspace=Path("/workspace"),
                        created_at=metadata.get("creationTimestamp", ""),
                        backend_type="openshift",
                        container_id=metadata.get("name", ""),
                    ))
            except json.JSONDecodeError:
                pass

        return sessions

    def sync_workspace(
        self,
        session_id: str,
        direction: str = "both",
        local_path: Path | None = None,
        remote_path: str = "/workspace",
        exclude: list[str] | None = None,
        verbose: bool = False,
    ) -> None:
        """Sync files between local and remote workspace using oc rsync.

        Args:
            session_id: ID of the session.
            direction: Sync direction ("local", "remote", "both").
                - "local": Sync from remote to local
                - "remote": Sync from local to remote
                - "both": Sync both directions (remote first, then local)
            local_path: Local directory to sync. Defaults to current directory.
            remote_path: Remote directory in the pod. Defaults to /workspace.
            exclude: List of patterns to exclude from sync.
            verbose: Whether to show rsync output (default False).
        """
        pod_name = f"paude-session-{session_id}"
        ns = self.namespace

        # Verify pod exists and is running
        result = self._run_oc(
            "get", "pod", pod_name,
            "-n", ns,
            "-o", "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            print(f"Session {session_id} not found.", file=sys.stderr)
            return

        phase = result.stdout.strip()
        if phase != "Running":
            print(
                f"Session {session_id} is not running (status: {phase}).",
                file=sys.stderr,
            )
            return

        local = local_path or Path.cwd()

        # Default excludes - exclude venvs and build artifacts, but NOT .git
        default_excludes = [
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
            ".paude-initialized",
        ]
        excludes = (exclude or []) + default_excludes

        # Build exclude args
        exclude_args: list[str] = []
        for pattern in excludes:
            exclude_args.extend(["--exclude", pattern])

        if direction in ("remote", "both"):
            print(
                f"Syncing local -> remote ({local} -> {pod_name}:{remote_path})...",
                file=sys.stderr,
            )
            self._rsync_to_pod(
                local, pod_name, remote_path, ns, exclude_args, verbose=verbose
            )

        if direction in ("local", "both"):
            print(
                f"Syncing remote -> local ({pod_name}:{remote_path} -> {local})...",
                file=sys.stderr,
            )
            self._rsync_from_pod(
                local, pod_name, remote_path, ns, exclude_args, verbose=verbose
            )

        print("Sync complete.", file=sys.stderr)

    def _rsync_to_pod(
        self,
        local_path: Path,
        pod_name: str,
        remote_path: str,
        namespace: str,
        exclude_args: list[str],
        verbose: bool = False,
    ) -> None:
        """Sync files from local to pod using oc rsync.

        Args:
            local_path: Local directory.
            pod_name: Pod name.
            remote_path: Remote path in pod.
            namespace: Kubernetes namespace.
            exclude_args: Exclude pattern arguments.
            verbose: Whether to show rsync output (default False).
        """
        cmd = ["oc", "rsync", "--progress"]

        if self._config.context:
            cmd.extend(["--context", self._config.context])

        cmd.extend(["-n", namespace])
        cmd.extend(exclude_args)

        # Add trailing slash to copy contents, not directory
        local_src = f"{local_path}/"
        remote_dest = f"{pod_name}:{remote_path}"

        cmd.extend([local_src, remote_dest])

        for attempt in range(1, self.RSYNC_MAX_RETRIES + 1):
            try:
                # Show output only when verbose is enabled
                result = subprocess.run(
                    cmd,
                    capture_output=not verbose,
                    text=True,
                    timeout=self.RSYNC_TIMEOUT,
                )
                if result.returncode != 0 and not verbose:
                    print(
                        f"Warning: rsync to pod failed: {result.stderr}",
                        file=sys.stderr,
                    )
                return
            except subprocess.TimeoutExpired:
                retries = self.RSYNC_MAX_RETRIES
                if attempt < retries:
                    print(
                        f"Rsync timed out (attempt {attempt}/{retries}), retrying...",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Rsync to pod failed after {retries} attempts",
                        file=sys.stderr,
                    )

    def _rsync_from_pod(
        self,
        local_path: Path,
        pod_name: str,
        remote_path: str,
        namespace: str,
        exclude_args: list[str],
        verbose: bool = False,
    ) -> None:
        """Sync files from pod to local using oc rsync.

        Args:
            local_path: Local directory.
            pod_name: Pod name.
            remote_path: Remote path in pod.
            namespace: Kubernetes namespace.
            exclude_args: Exclude pattern arguments.
            verbose: Whether to show rsync output (default False).
        """
        cmd = ["oc", "rsync", "--progress"]

        if self._config.context:
            cmd.extend(["--context", self._config.context])

        cmd.extend(["-n", namespace])
        cmd.extend(exclude_args)

        # Add trailing slash to copy contents, not directory
        remote_src = f"{pod_name}:{remote_path}/"
        local_dest = str(local_path)

        cmd.extend([remote_src, local_dest])

        for attempt in range(1, self.RSYNC_MAX_RETRIES + 1):
            try:
                # Show output only when verbose is enabled
                result = subprocess.run(
                    cmd,
                    capture_output=not verbose,
                    text=True,
                    timeout=self.RSYNC_TIMEOUT,
                )
                if result.returncode != 0 and not verbose:
                    print(
                        f"Warning: rsync from pod failed: {result.stderr}",
                        file=sys.stderr,
                    )
                return
            except subprocess.TimeoutExpired:
                retries = self.RSYNC_MAX_RETRIES
                if attempt < retries:
                    print(
                        f"Rsync timed out (attempt {attempt}/{retries}), retrying...",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Rsync from pod failed after {retries} attempts",
                        file=sys.stderr,
                    )
