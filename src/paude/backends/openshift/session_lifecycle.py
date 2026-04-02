"""Session lifecycle operations for OpenShift backend."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from paude.agents.base import Agent

from paude.backends.base import Session, SessionConfig
from paude.backends.openshift.build import BuildOrchestrator
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import SessionExistsError
from paude.backends.openshift.oc import OcClient
from paude.backends.openshift.pods import PodWaiter
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.openshift.resources import (
    StatefulSetBuilder,
    _generate_session_name,
)
from paude.backends.openshift.session_lookup import SessionLookup
from paude.backends.openshift.sync import ConfigSyncer
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_PROVIDER,
    build_session_env,
    pod_name,
    proxy_resource_name,
    pvc_name,
    resource_name,
)


def resolve_proxy_image(base_image: str) -> str:
    """Derive the proxy image reference from a base agent image."""
    from paude import __version__

    proxy_image = base_image.replace("paude-base-centos10", "paude-proxy-centos10")
    if proxy_image != base_image:
        return proxy_image

    registry = os.environ.get("PAUDE_REGISTRY", "quay.io/bbrowning")
    return f"{registry}/paude-proxy-centos10:{__version__}"


class SessionLifecycleManager:
    """Handles session create, delete, start, and stop operations."""

    def __init__(
        self,
        oc: OcClient,
        namespace: str,
        config: OpenShiftConfig,
        lookup: SessionLookup,
        syncer: ConfigSyncer,
        builder: BuildOrchestrator,
        proxy: ProxyManager,
        pod_waiter: PodWaiter,
    ) -> None:
        self._oc = oc
        self._namespace = namespace
        self._config = config
        self._lookup = lookup
        self._syncer = syncer
        self._builder = builder
        self._proxy = proxy
        self._pod_waiter = pod_waiter
        self._connect_fn: Callable[[str, str | None], int] | None = None

    def set_connect_fn(self, connect_fn: Callable[[str, str | None], int]) -> None:
        """Set the connect function for start_session delegation."""
        self._connect_fn = connect_fn

    def create_session(self, config: SessionConfig) -> Session:
        """Create a new persistent session.

        Creates Secrets, proxy Deployment, NetworkPolicies, and StatefulSet
        (replicas=1).  CA cert and credentials are stored as K8s Secrets
        and mounted into the pods declaratively — no ``oc exec`` orchestration.
        """
        self._oc.check_connection()
        self._oc.verify_namespace(self._namespace)

        session_name = config.name or _generate_session_name(config.workspace)

        if self._lookup.get_statefulset(session_name) is not None:
            raise SessionExistsError(f"Session '{session_name}' already exists")

        print(f"Creating session '{session_name}'...", file=sys.stderr)

        from paude.agents import get_agent as _get_agent

        agent = _get_agent(config.agent, provider=config.provider)
        ca_secret = self._setup_proxy(config, session_name, agent)
        session_env, secret_env = self._build_session_env(config, session_name)
        self._apply_and_wait(
            session_name, config, session_env, secret_env, ca_secret=ca_secret
        )

        session_status = "running" if config.wait_for_ready else "pending"
        print(f"Session '{session_name}' created.", file=sys.stderr)

        return Session(
            name=session_name,
            status=session_status,
            workspace=config.workspace,
            created_at=datetime.now(UTC).isoformat(),
            backend_type="openshift",
            container_id=pod_name(session_name),
            volume_name=pvc_name(session_name),
            agent=config.agent,
        )

    def _setup_proxy(
        self, config: SessionConfig, session_name: str, agent: Agent
    ) -> str:
        """Set up proxy Secrets, Deployment, Service, and NetworkPolicies.

        Returns:
            The CA Secret name.
        """
        proxy_image = self._resolve_proxy_image(config)

        from paude.backends.openshift.certs import (
            create_ca_secret,
            create_credentials_secret,
            generate_ca_cert,
        )
        from paude.backends.shared import (
            gather_proxy_credentials,
            local_gcp_adc_path,
        )

        # Generate CA cert and store as Secret
        cert_pem, key_pem = generate_ca_cert()
        ca_secret = create_ca_secret(
            self._oc, self._namespace, session_name, cert_pem, key_pem
        )

        # Store credentials as Secret (only proxy sees these)
        proxy_creds = gather_proxy_credentials(
            agent.config, gcp_adc_path=local_gcp_adc_path()
        )
        create_credentials_secret(self._oc, self._namespace, session_name, proxy_creds)

        self._proxy.create_deployment(
            session_name,
            proxy_image,
            config.allowed_domains,
            otel_ports=config.otel_ports,
        )
        self._proxy.create_service(session_name)
        self._proxy.ensure_proxy_network_policy(session_name)
        self._proxy.ensure_network_policy(session_name)
        return ca_secret

    def _resolve_proxy_image(self, config: SessionConfig) -> str:
        """Resolve the proxy image from config."""
        if config.proxy_image:
            return config.proxy_image

        return resolve_proxy_image(config.image)

    def _build_session_env(
        self, config: SessionConfig, session_name: str
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Build environment variables for the session.

        Returns:
            Tuple of (session_env, secret_env).
        """
        from paude.agents import get_agent
        from paude.backends.shared import PROXY_MANAGED_CREDENTIAL

        agent = get_agent(config.agent, provider=config.provider)
        # Agent gets dummy credential values; proxy handles real auth
        secret_env = dict.fromkeys(
            agent.config.secret_env_vars, PROXY_MANAGED_CREDENTIAL
        )
        proxy_name = proxy_resource_name(session_name)
        session_env, _agent_args = build_session_env(
            config, agent, proxy_name=proxy_name
        )

        return session_env, secret_env

    def _apply_and_wait(
        self,
        session_name: str,
        config: SessionConfig,
        session_env: dict[str, str],
        secret_env: dict[str, str],
        *,
        ca_secret: str | None = None,
    ) -> None:
        """Generate StatefulSet spec, apply it, wait for readiness, sync config."""
        ns = self._namespace
        sts_spec = self._generate_statefulset_spec(
            session_name=session_name,
            image=config.image,
            env=session_env,
            workspace=config.workspace,
            pvc_size=config.pvc_size,
            storage_class=config.storage_class,
            agent=config.agent,
            provider=config.provider,
            gpu=config.gpu,
            yolo=config.yolo,
            otel_endpoint=config.otel_endpoint,
            ca_secret=ca_secret,
        )

        print(
            f"Creating StatefulSet/{resource_name(session_name)} in namespace {ns}...",
            file=sys.stderr,
        )
        self._oc.run("apply", "-f", "-", input_data=json.dumps(sts_spec))

        if config.wait_for_ready:
            self._proxy.wait_for_ready(session_name)

            pname = pod_name(session_name)
            print(f"Waiting for pod {pname} to be ready...", file=sys.stderr)
            self._pod_waiter.wait_for_ready(pname)

            self._syncer.sync_full_config(
                pname,
                agent_name=config.agent,
                provider=config.provider,
                args=session_env.get("PAUDE_AGENT_ARGS", ""),
                yolo=config.yolo,
            )

    def start_agent_headless_in_pod(self, pname: str) -> None:
        """Start the agent in headless mode inside the pod."""
        from paude.backends.openshift.exceptions import OcTimeoutError
        from paude.backends.openshift.oc import OC_EXEC_TIMEOUT
        from paude.constants import CONTAINER_ENTRYPOINT

        try:
            result = self._oc.run(
                "exec",
                pname,
                "-n",
                self._namespace,
                "--",
                "env",
                "PAUDE_HEADLESS=1",
                CONTAINER_ENTRYPOINT,
                check=False,
                timeout=OC_EXEC_TIMEOUT,
            )
            if result.returncode != 0:
                print(
                    f"Warning: headless agent start failed (exit {result.returncode}). "
                    f"Agent will start on next 'paude connect'.",
                    file=sys.stderr,
                )
        except OcTimeoutError:
            print(
                "Warning: headless agent start timed out. "
                "Agent will start on next 'paude connect'.",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"Warning: headless agent start failed ({exc}). "
                f"Agent will start on next 'paude connect'.",
                file=sys.stderr,
            )

    def delete_session(self, name: str, confirm: bool = False) -> None:
        """Delete a session and all its resources.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If confirm=False.
        """
        if not confirm:
            raise ValueError("Deletion requires confirmation. Use --confirm flag.")

        self._lookup.require_session(name)

        ns = self._namespace
        sts_name = resource_name(name)
        pvc = pvc_name(name)

        print(f"Deleting session '{name}'...", file=sys.stderr)

        self._stop_port_forward(name)

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

        self._proxy.delete_resources(name)

        print(
            f"Deleting Build objects for session '{name}'...",
            file=sys.stderr,
        )
        self._builder.delete_session_builds(name)

        print(f"Session '{name}' deleted.", file=sys.stderr)

    def start_session(self, name: str, github_token: str | None = None) -> int:
        """Start a session and connect to it.

        Returns:
            Exit code from the connected session.
        """
        from paude.backends.openshift.exceptions import PodNotReadyError

        self._lookup.require_session(name)

        pname = pod_name(name)

        print(f"Starting session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 1)

        has_proxy = self._lookup.has_proxy_deployment(name)
        if has_proxy:
            self._refresh_proxy_credentials(name)
            self._scale_deployment(proxy_resource_name(name), 1)
            self._proxy.wait_for_ready(name)

        print(f"Waiting for Pod/{pname} to be ready...", file=sys.stderr)
        try:
            self._pod_waiter.wait_for_ready(pname)
        except PodNotReadyError as e:
            print(f"Pod failed to start: {e}", file=sys.stderr)
            return 1

        assert self._connect_fn is not None  # noqa: S101
        return self._connect_fn(name, github_token)

    def _refresh_proxy_credentials(self, session_name: str) -> None:
        """Update the proxy credential Secret with fresh host credentials."""
        from paude.backends.shared import gather_proxy_credentials, local_gcp_adc_path

        sts = self._lookup.get_statefulset(session_name)
        labels = sts.get("metadata", {}).get("labels", {}) if sts else {}
        agent_name = str(labels.get(PAUDE_LABEL_AGENT, "claude"))
        provider_val = labels.get(PAUDE_LABEL_PROVIDER)
        provider = str(provider_val) if provider_val is not None else None

        from paude.agents import get_agent

        agent = get_agent(agent_name, provider=provider)
        proxy_creds = gather_proxy_credentials(
            agent.config, gcp_adc_path=local_gcp_adc_path()
        )
        self._proxy.update_credentials(session_name, proxy_creds)

    def stop_session(self, name: str) -> None:
        """Stop a session (preserves volume)."""
        self._lookup.require_session(name)

        self._stop_port_forward(name)

        print(f"Stopping session '{name}'...", file=sys.stderr)
        self._scale_statefulset(name, 0)

        if self._lookup.has_proxy_deployment(name):
            proxy_dep = proxy_resource_name(name)
            print(f"Stopping proxy '{proxy_dep}'...", file=sys.stderr)
            self._scale_deployment(proxy_dep, 0)

        print(f"Session '{name}' stopped.", file=sys.stderr)

    def _stop_port_forward(self, session_name: str) -> None:
        """Stop any active port-forward for this session."""
        from paude.backends.openshift.port_forward import PortForwardManager

        mgr = PortForwardManager(self._namespace, self._config.context)
        mgr.stop(session_name)

    def _scale_statefulset(self, session_name: str, replicas: int) -> None:
        """Scale a StatefulSet to the specified number of replicas."""
        self._oc.run(
            "scale",
            "statefulset",
            resource_name(session_name),
            "-n",
            self._namespace,
            f"--replicas={replicas}",
        )

    def _scale_deployment(self, deployment_name: str, replicas: int) -> None:
        """Scale a Deployment to the specified number of replicas."""
        self._oc.run(
            "scale",
            "deployment",
            deployment_name,
            "-n",
            self._namespace,
            f"--replicas={replicas}",
            check=False,
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
        provider: str | None = None,
        gpu: str | None = None,
        yolo: bool = False,
        otel_endpoint: str | None = None,
        ca_secret: str | None = None,
    ) -> dict[str, Any]:
        """Generate a Kubernetes StatefulSet specification."""
        builder = (
            StatefulSetBuilder(
                session_name=session_name,
                namespace=self._namespace,
                image=image,
                resources=self._config.resources,
                agent=agent,
                provider=provider,
                gpu=gpu,
                yolo=yolo,
            )
            .with_env(env)
            .with_workspace(workspace)
            .with_pvc(size=pvc_size, storage_class=storage_class)
            .with_otel_endpoint(otel_endpoint)
        )
        if ca_secret:
            builder = builder.with_ca_secret(ca_secret)
        return builder.build()
