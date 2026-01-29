"""OpenShift backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.base import Session, SessionConfig
from paude.backends.openshift.build import BuildOrchestrator
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import (
    PodNotReadyError,
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.openshift.oc import (
    OC_DEFAULT_TIMEOUT,
    OC_EXEC_TIMEOUT,
    OcClient,
)
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.openshift.resources import (
    StatefulSetBuilder,
    _decode_path,
    _generate_session_name,
)
from paude.backends.openshift.sync import ConfigSyncer


class OpenShiftBackend:
    """OpenShift container backend.

    This backend runs Claude in pods on an OpenShift cluster. Sessions are
    persistent and can survive network disconnections using tmux.
    """

    # Class-level constants for backward compatibility
    OC_DEFAULT_TIMEOUT = OC_DEFAULT_TIMEOUT
    OC_EXEC_TIMEOUT = OC_EXEC_TIMEOUT

    def __init__(self, config: OpenShiftConfig | None = None) -> None:
        """Initialize the OpenShift backend.

        Args:
            config: OpenShift configuration. Defaults to OpenShiftConfig().
        """
        self._config = config or OpenShiftConfig()
        self._oc = OcClient(self._config)
        self._syncer_instance: ConfigSyncer | None = None
        self._builder_instance: BuildOrchestrator | None = None
        self._proxy_instance: ProxyManager | None = None
        self._resolved_namespace: str | None = None

    @property
    def _syncer(self) -> ConfigSyncer:
        """Lazy-initialized ConfigSyncer instance."""
        if self._syncer_instance is None:
            self._syncer_instance = ConfigSyncer(self._oc, self.namespace)
        return self._syncer_instance

    @property
    def _builder(self) -> BuildOrchestrator:
        """Lazy-initialized BuildOrchestrator instance."""
        if self._builder_instance is None:
            self._builder_instance = BuildOrchestrator(
                self._oc, self.namespace, self._config
            )
        return self._builder_instance

    @property
    def _proxy(self) -> ProxyManager:
        """Lazy-initialized ProxyManager instance."""
        if self._proxy_instance is None:
            self._proxy_instance = ProxyManager(self._oc, self.namespace)
        return self._proxy_instance

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
            self._resolved_namespace = self._oc.get_current_namespace()

        return self._resolved_namespace

    def _run_oc(
        self,
        *args: str,
        capture: bool = True,
        check: bool = True,
        input_data: str | None = None,
        timeout: int | None = None,
        namespace: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run an oc command (delegates to OcClient).

        This method is kept for backward compatibility. New code should
        use self._oc.run() directly.
        """
        return self._oc.run(
            *args,
            capture=capture,
            check=check,
            input_data=input_data,
            timeout=timeout,
            namespace=namespace,
        )

    def _check_connection(self) -> bool:
        """Check if logged in to OpenShift (delegates to OcClient)."""
        return self._oc.check_connection()

    def _get_current_namespace(self) -> str:
        """Get the current namespace from oc config (delegates to OcClient)."""
        return self._oc.get_current_namespace()

    def _verify_namespace(self) -> None:
        """Verify the target namespace exists (delegates to OcClient)."""
        self._oc.verify_namespace(self.namespace)

    def _create_build_config(
        self,
        config_hash: str,
        name_prefix: str = "paude",
    ) -> None:
        """Create a BuildConfig (delegates to BuildOrchestrator)."""
        self._builder.create_build_config(config_hash, name_prefix)

    def _start_binary_build(
        self,
        config_hash: str,
        context_dir: Path,
        session_name: str | None = None,
        name_prefix: str = "paude",
    ) -> str:
        """Start a binary build (delegates to BuildOrchestrator)."""
        return self._builder.start_binary_build(
            config_hash, context_dir, session_name, name_prefix
        )

    def _wait_for_build(self, build_name: str, timeout: int = 600) -> None:
        """Wait for a build to complete (delegates to BuildOrchestrator)."""
        self._builder.wait_for_build(build_name, timeout)

    def _get_imagestream_reference(
        self,
        config_hash: str,
        name_prefix: str = "paude",
    ) -> str:
        """Get imagestream reference (delegates to BuildOrchestrator)."""
        return self._builder.get_imagestream_reference(config_hash, name_prefix)

    def ensure_image_via_build(
        self,
        config: Any,
        workspace: Path,
        script_dir: Path | None = None,
        force_rebuild: bool = False,
        session_name: str | None = None,
    ) -> str:
        """Ensure image via build (delegates to BuildOrchestrator)."""
        return self._builder.ensure_image_via_build(
            config, workspace, script_dir, force_rebuild, session_name
        )

    def ensure_proxy_image_via_build(
        self,
        script_dir: Path,
        force_rebuild: bool = False,
        session_name: str | None = None,
    ) -> str:
        """Ensure proxy image via build (delegates to BuildOrchestrator)."""
        return self._builder.ensure_proxy_image_via_build(
            script_dir, force_rebuild, session_name
        )

    def _ensure_network_policy(self, session_id: str) -> None:
        """Ensure a NetworkPolicy exists (delegates to ProxyManager)."""
        self._proxy.ensure_network_policy(session_id)

    def _ensure_network_policy_permissive(self, session_id: str) -> None:
        """Ensure a permissive NetworkPolicy exists (delegates to ProxyManager)."""
        self._proxy.ensure_network_policy_permissive(session_id)

    def _create_proxy_deployment(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None = None,
    ) -> None:
        """Create a proxy Deployment (delegates to ProxyManager)."""
        self._proxy.create_deployment(session_name, proxy_image, allowed_domains)

    def _create_proxy_service(self, session_name: str) -> str:
        """Create a proxy Service (delegates to ProxyManager)."""
        return self._proxy.create_service(session_name)

    def _wait_for_proxy_ready(
        self,
        session_name: str,
        timeout: int = 120,
    ) -> None:
        """Wait for the proxy deployment to be ready (delegates to ProxyManager)."""
        self._proxy.wait_for_ready(session_name, timeout)

    def _delete_proxy_resources(self, session_name: str) -> None:
        """Delete proxy resources (delegates to ProxyManager)."""
        self._proxy.delete_resources(session_name)

    def _delete_session_builds(self, session_name: str) -> None:
        """Delete Build objects (delegates to BuildOrchestrator)."""
        print(
            f"Deleting Build objects for session '{session_name}'...",
            file=sys.stderr,
        )
        self._builder.delete_session_builds(session_name)

    def _ensure_proxy_network_policy(self, session_name: str) -> None:
        """Create a NetworkPolicy for the proxy pod (delegates to ProxyManager)."""
        self._proxy.ensure_proxy_network_policy(session_name)

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

        Delegates to StatefulSetBuilder for actual spec generation.

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
        return (
            StatefulSetBuilder(
                session_name=session_name,
                namespace=self.namespace,
                image=image,
                resources=self._config.resources,
            )
            .with_env(env)
            .with_workspace(workspace)
            .with_pvc(size=pvc_size, storage_class=storage_class)
            .build()
        )

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
        # allowed_domains is None → no proxy (permissive NetworkPolicy)
        # allowed_domains is list → create proxy with those domains
        if config.allowed_domains is not None:
            # Create proxy pod and service first (before NetworkPolicy)
            # Use provided proxy_image or derive from the main image
            if config.proxy_image:
                proxy_image = config.proxy_image
            else:
                proxy_image = config.image.replace(
                    "paude-base-centos9", "paude-proxy-centos9"
                )
                # If image doesn't contain the expected pattern, use a default
                if proxy_image == config.image:
                    proxy_image = "quay.io/bbrowning/paude-proxy-centos9:latest"

            self._create_proxy_deployment(
                session_name, proxy_image, config.allowed_domains
            )
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

        # Add proxy environment variables when using proxy (allowed_domains is set)
        if config.allowed_domains is not None:
            proxy_url = f"http://paude-proxy-{session_name}:3128"
            session_env["HTTP_PROXY"] = proxy_url
            session_env["HTTPS_PROXY"] = proxy_url
            session_env["http_proxy"] = proxy_url
            session_env["https_proxy"] = proxy_url

        # Generate and apply StatefulSet spec
        # Credentials are synced to /credentials (tmpfs) when session starts
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

        # Wait for proxy to be ready first (if using proxy)
        if config.allowed_domains is not None:
            self._wait_for_proxy_ready(session_name)

        # Wait for pod to be ready
        pod_name = f"paude-{session_name}-0"
        print(f"Waiting for pod {pod_name} to be ready...", file=sys.stderr)
        self._wait_for_pod_ready(pod_name)

        # Sync configuration and credentials
        self._sync_config_to_pod(pod_name)

        print(f"Session '{session_name}' created.", file=sys.stderr)

        return Session(
            name=session_name,
            status="running",
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

    def _is_config_synced(self, pod_name: str) -> bool:
        """Check if configuration has already been synced to the pod."""
        return self._syncer.is_config_synced(pod_name)

    def _sync_credentials_to_pod(
        self,
        pod_name: str,
        verbose: bool = False,
    ) -> None:
        """Refresh gcloud credentials on the pod (delegates to ConfigSyncer)."""
        self._syncer.sync_credentials(pod_name, verbose=verbose)

    def _sync_config_to_pod(
        self,
        pod_name: str,
        verbose: bool = False,
    ) -> None:
        """Sync all configuration to pod (delegates to ConfigSyncer)."""
        self._syncer.sync_full_config(pod_name, verbose=verbose)

    def _rewrite_plugin_paths(self, pod_name: str, config_path: str) -> None:
        """Rewrite plugin paths (delegates to ConfigSyncer)."""
        self._syncer._rewrite_plugin_paths(pod_name, config_path)

    def start_session(self, name: str) -> int:
        """Start a session and connect to it.

        Scales StatefulSet to 1 and connects.

        Args:
            name: Session name.

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

        # Note: Credentials are synced in connect_session() which is called below.
        # This ensures credentials are refreshed on every connect, not just start.

        # Connect to session
        return self.connect_session(name)

    def stop_session(self, name: str) -> None:
        """Stop a session (preserves volume).

        Scales StatefulSet to 0 but keeps PVC intact.

        Args:
            name: Session name.
        """
        # Check if session exists
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")

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

        On first connect: syncs full configuration (gcloud, claude, git).
        On reconnect: only refreshes gcloud credentials (fast).

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

        # Check if this is first connect or reconnect
        if self._is_config_synced(pod_name):
            # Reconnect: only refresh gcloud credentials (fast)
            self._sync_credentials_to_pod(pod_name, verbose=False)
        else:
            # First connect: full config sync (gcloud + claude + git)
            self._sync_config_to_pod(pod_name, verbose=False)

        # Check if workspace is empty (no .git directory)
        check_result = self._run_oc(
            "exec", pod_name, "-n", ns, "--",
            "test", "-d", "/pvc/workspace/.git",
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )
        if check_result.returncode != 0:
            print("", file=sys.stderr)
            print("Workspace is empty. To sync code:", file=sys.stderr)
            print(f"  paude remote add {name}", file=sys.stderr)
            print(f"  git push paude-{name} main", file=sys.stderr)
            print("", file=sys.stderr)

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
