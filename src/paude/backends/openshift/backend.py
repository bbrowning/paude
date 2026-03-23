"""OpenShift backend implementation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from paude.agents.base import Agent

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
    RSYNC_TIMEOUT,
    OcClient,
)
from paude.backends.openshift.pods import PodWaiter
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.openshift.resources import (
    StatefulSetBuilder,
    _generate_session_name,
)
from paude.backends.openshift.sync import ConfigSyncer
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    SQUID_BLOCKED_LOG_PATH,
    build_session_env,
    decode_path,
    pod_name,
    proxy_resource_name,
    pvc_name,
    resource_name,
)


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
        self._pod_waiter_instance: PodWaiter | None = None
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
    def _pod_waiter(self) -> PodWaiter:
        """Lazy-initialized PodWaiter instance."""
        if self._pod_waiter_instance is None:
            self._pod_waiter_instance = PodWaiter(self._oc, self.namespace)
        return self._pod_waiter_instance

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

    def ensure_image_via_build(
        self,
        config: Any,
        workspace: Path,
        script_dir: Path | None = None,
        force_rebuild: bool = False,
        session_name: str | None = None,
        agent: Agent | None = None,
    ) -> str:
        """Ensure image via build (delegates to BuildOrchestrator)."""
        return self._builder.ensure_image_via_build(
            config,
            workspace,
            script_dir,
            force_rebuild,
            session_name,
            agent=agent,
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

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_statefulset(self, session_name: str) -> dict[str, Any] | None:
        """Get StatefulSet for a session.

        Args:
            session_name: Session name.

        Returns:
            StatefulSet data or None if not found.
        """
        sts_name = resource_name(session_name)

        result = self._oc.run(
            "get",
            "statefulset",
            sts_name,
            "-n",
            self.namespace,
            "-o",
            "json",
            check=False,
        )

        if result.returncode != 0:
            return None

        try:
            data: dict[str, Any] = json.loads(result.stdout)
            return data
        except json.JSONDecodeError:
            return None

    def _require_session(self, name: str) -> dict[str, Any]:
        """Get StatefulSet for a session, raising if not found.

        Args:
            name: Session name.

        Returns:
            StatefulSet data.

        Raises:
            SessionNotFoundError: If session not found.
        """
        sts = self._get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")
        return sts

    def _get_pod_for_session(self, session_name: str) -> str | None:
        """Get the pod name for a session if it exists.

        For StatefulSets, the pod name is predictable: {sts-name}-0.

        Args:
            session_name: Session name.

        Returns:
            Pod name or None if not found.
        """
        pname = pod_name(session_name)

        result = self._oc.run(
            "get",
            "pod",
            pname,
            "-n",
            self.namespace,
            "-o",
            "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            return None

        return pname

    def _require_running_pod(self, name: str) -> str:
        """Get pod name for a session, raising if not found or not running.

        Args:
            name: Session name.

        Returns:
            Pod name.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        self._require_session(name)
        pname = self._get_pod_for_session(name)
        if pname is None:
            raise ValueError(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it."
            )
        return pname

    def _has_proxy_deployment(self, session_name: str) -> bool:
        """Check if a proxy deployment exists for a session.

        Args:
            session_name: Session name.

        Returns:
            True if proxy deployment exists.
        """
        result = self._oc.run(
            "get",
            "deployment",
            proxy_resource_name(session_name),
            "-n",
            self.namespace,
            check=False,
        )
        return result.returncode == 0

    def _scale_statefulset(self, session_name: str, replicas: int) -> None:
        """Scale a StatefulSet to the specified number of replicas."""
        self._oc.run(
            "scale",
            "statefulset",
            resource_name(session_name),
            "-n",
            self.namespace,
            f"--replicas={replicas}",
        )

    def _scale_deployment(self, deployment_name: str, replicas: int) -> None:
        """Scale a Deployment to the specified number of replicas."""
        self._oc.run(
            "scale",
            "deployment",
            deployment_name,
            "-n",
            self.namespace,
            f"--replicas={replicas}",
            check=False,  # Don't fail if deployment doesn't exist
        )

    def _generate_statefulset_spec(
        self,
        session_name: str,
        image: str,
        env: dict[str, str],
        workspace: Path,
        pvc_size: str = "10Gi",
        storage_class: str | None = None,
        agent: str = "claude",
        gpu: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Kubernetes StatefulSet specification."""
        return (
            StatefulSetBuilder(
                session_name=session_name,
                namespace=self.namespace,
                image=image,
                resources=self._config.resources,
                agent=agent,
                gpu=gpu,
            )
            .with_env(env)
            .with_workspace(workspace)
            .with_pvc(size=pvc_size, storage_class=storage_class)
            .build()
        )

    def _session_from_statefulset(
        self, sts: dict[str, Any], name: str | None = None
    ) -> Session:
        """Build a Session object from a StatefulSet dict.

        Args:
            sts: StatefulSet data from Kubernetes.
            name: Session name override. If None, extracted from labels.

        Returns:
            Session object.
        """
        metadata = sts.get("metadata", {})
        labels = metadata.get("labels", {})
        annotations = metadata.get("annotations", {})
        spec = sts.get("spec", {})

        session_name = name or labels.get("paude.io/session-name", "unknown")

        # Determine status from replicas
        replicas = spec.get("replicas", 0)
        ready_replicas = sts.get("status", {}).get("readyReplicas", 0)

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
                decode_path(workspace_encoded)
                if workspace_encoded
                else Path("/workspace")
            )
        except Exception:
            workspace = Path("/workspace")

        created_at = annotations.get(
            "paude.io/created-at", metadata.get("creationTimestamp", "")
        )

        return Session(
            name=session_name,
            status=status,
            workspace=workspace,
            created_at=created_at,
            backend_type="openshift",
            container_id=pod_name(session_name),
            volume_name=pvc_name(session_name),
            agent=labels.get(PAUDE_LABEL_AGENT, "claude"),
        )

    # -------------------------------------------------------------------------
    # Backend Protocol Methods (persistent sessions)
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
        self._oc.check_connection()

        # Verify namespace exists
        self._oc.verify_namespace(self.namespace)

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
                    "paude-base-centos10", "paude-proxy-centos10"
                )
                # If image doesn't contain the expected pattern, use a default
                if proxy_image == config.image:
                    proxy_image = "quay.io/bbrowning/paude-proxy-centos10:latest"

            self._proxy.create_deployment(
                session_name, proxy_image, config.allowed_domains
            )
            self._proxy.create_service(session_name)

            # Create NetworkPolicy for proxy (allows all egress for squid)
            self._proxy.ensure_proxy_network_policy(session_name)

            # Now create NetworkPolicy that allows traffic to the proxy
            self._proxy.ensure_network_policy(session_name)
        else:
            self._proxy.ensure_network_policy_permissive(session_name)

        # Build environment variables
        from paude.agents import get_agent
        from paude.agents.base import build_secret_environment_from_config

        agent = get_agent(config.agent)
        secret_env = build_secret_environment_from_config(agent.config)
        proxy_name = (
            proxy_resource_name(session_name)
            if config.allowed_domains is not None
            else None
        )
        session_env, _agent_args = build_session_env(
            config, agent, proxy_name=proxy_name
        )

        # Add credential watchdog environment variables
        session_env["PAUDE_CREDENTIAL_TIMEOUT"] = str(config.credential_timeout)
        session_env["PAUDE_CREDENTIAL_WATCHDOG"] = (
            "1" if config.credential_timeout > 0 else "0"
        )

        # Generate and apply StatefulSet spec
        # Credentials are synced to /credentials (tmpfs) when session starts
        sts_spec = self._generate_statefulset_spec(
            session_name=session_name,
            image=config.image,
            env=session_env,
            workspace=config.workspace,
            pvc_size=config.pvc_size,
            storage_class=config.storage_class,
            agent=config.agent,
            gpu=config.gpu,
        )

        print(
            f"Creating StatefulSet/{resource_name(session_name)} in namespace {ns}...",
            file=sys.stderr,
        )
        self._oc.run(
            "apply",
            "-f",
            "-",
            input_data=json.dumps(sts_spec),
        )

        if config.wait_for_ready:
            # Wait for proxy to be ready first (if using proxy)
            if config.allowed_domains is not None:
                self._proxy.wait_for_ready(session_name)

            # Wait for pod to be ready
            pname = pod_name(session_name)
            print(f"Waiting for pod {pname} to be ready...", file=sys.stderr)
            self._pod_waiter.wait_for_ready(pname)

            # Sync configuration and credentials
            self._syncer.sync_full_config(
                pname, agent_name=config.agent, secret_env=secret_env
            )

        session_status = "running" if config.wait_for_ready else "pending"
        print(f"Session '{session_name}' created.", file=sys.stderr)

        return Session(
            name=session_name,
            status=session_status,
            workspace=config.workspace,
            created_at=created_at,
            backend_type="openshift",
            container_id=pod_name(session_name),
            volume_name=pvc_name(session_name),
            agent=config.agent,
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

        self._require_session(name)

        ns = self.namespace
        sts_name = resource_name(name)
        pvc = pvc_name(name)

        print(f"Deleting session '{name}'...", file=sys.stderr)

        # Scale to 0 first to gracefully stop pod
        print(f"Scaling StatefulSet/{sts_name} to 0...", file=sys.stderr)
        self._oc.run(
            "scale",
            "statefulset",
            sts_name,
            "-n",
            ns,
            "--replicas=0",
            check=False,
        )

        # Delete StatefulSet
        print(f"Deleting StatefulSet/{sts_name}...", file=sys.stderr)
        self._oc.run(
            "delete",
            "statefulset",
            sts_name,
            "-n",
            ns,
            "--grace-period=0",
            check=False,
        )

        # Delete PVC (volumeClaimTemplates don't delete PVCs automatically)
        # Use longer timeout since PVC deletion waits for pod termination
        print(f"Deleting PVC/{pvc}...", file=sys.stderr)
        self._oc.run(
            "delete",
            "pvc",
            pvc,
            "-n",
            ns,
            check=False,
            timeout=90,
        )

        # Delete session-specific NetworkPolicies
        print("Deleting NetworkPolicy for session...", file=sys.stderr)
        self._oc.run(
            "delete",
            "networkpolicy",
            "-n",
            ns,
            "-l",
            f"paude.io/session-name={name}",
            check=False,
        )

        # Delete proxy Deployment and Service (if they exist)
        self._proxy.delete_resources(name)

        # Delete Build objects created for this session
        print(
            f"Deleting Build objects for session '{name}'...",
            file=sys.stderr,
        )
        self._builder.delete_session_builds(name)

        print(f"Session '{name}' deleted.", file=sys.stderr)

    def start_session(self, name: str, github_token: str | None = None) -> int:
        """Start a session and connect to it.

        Scales StatefulSet to 1 and connects.

        Args:
            name: Session name.
            github_token: Optional GitHub token to inject into pod credentials tmpfs.
                Falls back to PAUDE_GITHUB_TOKEN env var if not provided.

        Returns:
            Exit code from the connected session.
        """
        self._require_session(name)

        pname = pod_name(name)

        # Scale to 1
        print(f"Starting session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 1)

        # Scale proxy up if it exists
        if self._has_proxy_deployment(name):
            self._scale_deployment(proxy_resource_name(name), 1)
            self._proxy.wait_for_ready(name)

        # Wait for pod to be ready
        print(f"Waiting for Pod/{pname} to be ready...", file=sys.stderr)
        try:
            self._pod_waiter.wait_for_ready(pname)
        except PodNotReadyError as e:
            print(f"Pod failed to start: {e}", file=sys.stderr)
            return 1

        # Note: Credentials are synced in connect_session() which is called below.
        # This ensures credentials are refreshed on every connect, not just start.

        # Connect to session
        return self.connect_session(name, github_token=github_token)

    def stop_session(self, name: str) -> None:
        """Stop a session (preserves volume).

        Scales StatefulSet to 0 but keeps PVC intact.

        Args:
            name: Session name.
        """
        self._require_session(name)

        # Scale to 0
        print(f"Stopping session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 0)

        # Scale proxy to 0 if it exists
        if self._has_proxy_deployment(name):
            proxy_dep = proxy_resource_name(name)
            print(f"Stopping proxy '{proxy_dep}'...", file=sys.stderr)
            self._scale_deployment(proxy_dep, 0)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        """Attach to a running session.

        On first connect: syncs full configuration (gcloud, claude, git).
        On reconnect: only refreshes gcloud credentials (fast).

        Args:
            name: Session name.
            github_token: Optional GitHub token to inject into pod credentials tmpfs.
                Falls back to PAUDE_GITHUB_TOKEN env var if not provided.

        Returns:
            Exit code from the attached session.
        """
        pname = self._get_pod_for_session(name)
        if pname is None:
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return 1

        ns = self.namespace

        # Verify pod is running (not just existing)
        result = self._oc.run(
            "get",
            "pod",
            pname,
            "-n",
            ns,
            "-o",
            "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0 or result.stdout.strip() != "Running":
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return 1

        # Collect secret env vars for the agent
        from paude.agents.base import build_secret_environment_from_config

        sts = self._get_statefulset(name)
        sts_labels = sts.get("metadata", {}).get("labels", {}) if sts else {}
        agent_name = sts_labels.get(PAUDE_LABEL_AGENT, "claude")

        from paude.agents import get_agent

        agent = get_agent(agent_name)
        secret_env = build_secret_environment_from_config(agent.config)

        # Check if this is first connect or reconnect
        if self._syncer.is_config_synced(pname):
            # Reconnect: only refresh gcloud credentials (fast)
            self._syncer.sync_credentials(
                pname,
                verbose=False,
                github_token=github_token,
                secret_env=secret_env,
                agent_name=agent_name,
            )
        else:
            # First connect: full config sync (gcloud + agent + git)
            self._syncer.sync_full_config(
                pname,
                verbose=False,
                github_token=github_token,
                agent_name=agent_name,
                secret_env=secret_env,
            )

        # Check if workspace is empty (no .git directory)
        check_result = self._oc.run(
            "exec",
            pname,
            "-n",
            ns,
            "--",
            "test",
            "-d",
            "/pvc/workspace/.git",
            check=False,
            timeout=self.OC_EXEC_TIMEOUT,
        )
        if check_result.returncode != 0:
            print("", file=sys.stderr)
            print("Workspace is empty. To sync code:", file=sys.stderr)
            print(f"  paude remote add {name}", file=sys.stderr)
            print(f"  git push {resource_name(name)} main", file=sys.stderr)
            print("", file=sys.stderr)

        # Attach using oc exec with interactive TTY
        exec_cmd = ["oc", "exec", "-it", "-n", ns, pname, "--"]

        if self._config.context:
            exec_cmd = [
                "oc",
                "--context",
                self._config.context,
                "exec",
                "-it",
                "-n",
                ns,
                pname,
                "--",
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
        return self._session_from_statefulset(sts, name=name)

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

    def get_allowed_domains(self, name: str) -> list[str] | None:
        """Get current allowed domains for a session.

        Args:
            name: Session name.

        Returns:
            List of domains, or None if session has no proxy (unrestricted).

        Raises:
            SessionNotFoundError: If session not found.
        """
        self._require_session(name)

        if not self._has_proxy_deployment(name):
            return None  # No proxy = unrestricted

        return self._proxy.get_deployment_domains(name)

    def get_proxy_blocked_log(self, name: str) -> str | None:
        """Get raw squid blocked log from the proxy container.

        Returns:
            Raw log content, empty string if no blocks yet,
            or None if no proxy (unrestricted).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If proxy is not running.
        """
        self._require_session(name)

        if not self._has_proxy_deployment(name):
            return None

        # Find proxy pod
        pod_result = self._oc.run(
            "get",
            "pods",
            "-l",
            f"app=paude-proxy,paude.io/session-name={name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
            "-n",
            self.namespace,
            check=False,
        )
        if pod_result.returncode != 0 or not pod_result.stdout.strip():
            raise ValueError(f"Proxy for session '{name}' is not running.")

        proxy_pod = pod_result.stdout.strip()
        log_result = self._oc.run(
            "exec",
            proxy_pod,
            "-n",
            self.namespace,
            "--",
            "cat",
            SQUID_BLOCKED_LOG_PATH,
            check=False,
        )
        if log_result.returncode != 0:
            return ""
        return log_result.stdout

    def update_allowed_domains(self, name: str, domains: list[str]) -> None:
        """Update allowed domains for a session.

        Args:
            name: Session name.
            domains: New list of allowed domains.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session has no proxy deployment.
        """
        self._require_session(name)

        if not self._has_proxy_deployment(name):
            raise ValueError(
                f"Session '{name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        self._proxy.update_deployment_domains(name, domains)

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        """Execute a command inside a running session's container.

        Args:
            name: Session name.
            command: Shell command to execute.

        Returns:
            Tuple of (return_code, stdout, stderr).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._require_running_pod(name)

        result = self._oc.run(
            "exec",
            pname,
            "-n",
            self.namespace,
            "--",
            "bash",
            "-c",
            command,
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        return (result.returncode, result.stdout, result.stderr)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        """Copy a file or directory from local to a running session.

        Args:
            name: Session name.
            local_path: Local file or directory path.
            remote_path: Destination path inside the container.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._require_running_pod(name)

        self._oc.run(
            "cp",
            local_path,
            f"{pname}:{remote_path}",
            "-n",
            self.namespace,
            timeout=RSYNC_TIMEOUT,
        )

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        """Copy a file or directory from a running session to local.

        Args:
            name: Session name.
            remote_path: Source path inside the container.
            local_path: Local destination path.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._require_running_pod(name)

        self._oc.run(
            "cp",
            f"{pname}:{remote_path}",
            local_path,
            "-n",
            self.namespace,
            timeout=RSYNC_TIMEOUT,
        )

    def list_sessions(self) -> list[Session]:
        """List all sessions (StatefulSets).

        Returns:
            List of Session objects.
        """
        result = self._oc.run(
            "get",
            "statefulsets",
            "-n",
            self.namespace,
            "-l",
            "app=paude",
            "-o",
            "json",
            check=False,
        )

        if result.returncode != 0:
            return []

        try:
            data = json.loads(result.stdout)
            return [
                self._session_from_statefulset(item) for item in data.get("items", [])
            ]
        except json.JSONDecodeError:
            return []
