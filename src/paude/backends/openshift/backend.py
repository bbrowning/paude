"""OpenShift backend implementation (facade)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from paude.agents.base import Agent

from paude.backends.base import Session, SessionConfig
from paude.backends.openshift.build import BuildOrchestrator
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.oc import OC_DEFAULT_TIMEOUT, OC_EXEC_TIMEOUT, OcClient
from paude.backends.openshift.pods import PodWaiter
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.openshift.session_connection import SessionConnector
from paude.backends.openshift.session_domains import SessionDomainManager
from paude.backends.openshift.session_lifecycle import SessionLifecycleManager
from paude.backends.openshift.session_lookup import SessionLookup


class OpenShiftBackend:
    """OpenShift container backend.

    This backend runs Claude in pods on an OpenShift cluster. Sessions are
    persistent and can survive network disconnections using tmux.

    This class is a thin facade that delegates to focused manager classes.
    """

    # Class-level constants for backward compatibility
    OC_DEFAULT_TIMEOUT = OC_DEFAULT_TIMEOUT
    OC_EXEC_TIMEOUT = OC_EXEC_TIMEOUT

    def __init__(self, config: OpenShiftConfig | None = None) -> None:
        self._config = config or OpenShiftConfig()
        self._oc = OcClient(self._config)
        self._resolved_namespace: str | None = None

        # Lazy-initialized collaborators
        self._lookup_instance: SessionLookup | None = None
        self._builder_instance: BuildOrchestrator | None = None
        self._proxy_instance: ProxyManager | None = None
        self._pod_waiter_instance: PodWaiter | None = None
        self._lifecycle_instance: SessionLifecycleManager | None = None
        self._connector_instance: SessionConnector | None = None
        self._domains_instance: SessionDomainManager | None = None

    @property
    def namespace(self) -> str:
        """Get the resolved namespace."""
        if self._resolved_namespace is not None:
            return self._resolved_namespace

        if self._config.namespace:
            self._resolved_namespace = self._config.namespace
        else:
            self._resolved_namespace = self._oc.get_current_namespace()

        return self._resolved_namespace

    @property
    def _lookup(self) -> SessionLookup:
        if self._lookup_instance is None:
            self._lookup_instance = SessionLookup(self._oc, self.namespace)
        return self._lookup_instance

    @property
    def _builder(self) -> BuildOrchestrator:
        if self._builder_instance is None:
            self._builder_instance = BuildOrchestrator(
                self._oc, self.namespace, self._config
            )
        return self._builder_instance

    @property
    def _proxy(self) -> ProxyManager:
        if self._proxy_instance is None:
            self._proxy_instance = ProxyManager(self._oc, self.namespace)
        return self._proxy_instance

    @property
    def _pod_waiter(self) -> PodWaiter:
        if self._pod_waiter_instance is None:
            self._pod_waiter_instance = PodWaiter(self._oc, self.namespace)
        return self._pod_waiter_instance

    @property
    def _connector(self) -> SessionConnector:
        if self._connector_instance is None:
            self._connector_instance = SessionConnector(
                self._oc, self.namespace, self._config, self._lookup
            )
        return self._connector_instance

    @property
    def _lifecycle(self) -> SessionLifecycleManager:
        if self._lifecycle_instance is None:
            self._lifecycle_instance = SessionLifecycleManager(
                self._oc,
                self.namespace,
                self._config,
                self._lookup,
                self._builder,
                self._proxy,
                self._pod_waiter,
            )
            self._lifecycle_instance.set_connect_fn(
                lambda name, github_token: self.connect_session(
                    name, github_token=github_token
                )
            )
        return self._lifecycle_instance

    @property
    def _domains(self) -> SessionDomainManager:
        if self._domains_instance is None:
            self._domains_instance = SessionDomainManager(
                self._oc, self.namespace, self._lookup, self._proxy
            )
        return self._domains_instance

    # -------------------------------------------------------------------------
    # Internal delegations (used by tests)
    # -------------------------------------------------------------------------

    def _get_statefulset(self, session_name: str) -> dict[str, Any] | None:
        return self._lookup.get_statefulset(session_name)

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
        return self._lifecycle._generate_statefulset_spec(
            session_name=session_name,
            image=image,
            env=env,
            workspace=workspace,
            pvc_size=pvc_size,
            storage_class=storage_class,
            agent=agent,
            gpu=gpu,
        )

    # -------------------------------------------------------------------------
    # Build delegation
    # -------------------------------------------------------------------------

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
            config, workspace, script_dir, force_rebuild, session_name, agent=agent
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
    # Session lifecycle
    # -------------------------------------------------------------------------

    def create_session(self, config: SessionConfig) -> Session:
        return self._lifecycle.create_session(config)

    def delete_session(self, name: str, confirm: bool = False) -> None:
        self._lifecycle.delete_session(name, confirm)

    def start_session(self, name: str, github_token: str | None = None) -> int:
        return self._lifecycle.start_session(name, github_token)

    def stop_session(self, name: str) -> None:
        self._lifecycle.stop_session(name)

    def start_agent_headless(self, name: str) -> None:
        """No-op: agent starts automatically via entrypoint."""

    # -------------------------------------------------------------------------
    # Connection and exec
    # -------------------------------------------------------------------------

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        return self._connector.connect_session(name, github_token)

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        return self._connector.exec_in_session(name, command)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        self._connector.copy_to_session(name, local_path, remote_path)

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        self._connector.copy_from_session(name, remote_path, local_path)

    # -------------------------------------------------------------------------
    # Session queries
    # -------------------------------------------------------------------------

    def get_session(self, name: str) -> Session | None:
        return self._lookup.get_session(name)

    def find_session_for_workspace(self, workspace: Path) -> Session | None:
        return self._lookup.find_session_for_workspace(workspace)

    def list_sessions(self) -> list[Session]:
        return self._lookup.list_sessions()

    # -------------------------------------------------------------------------
    # Domain management
    # -------------------------------------------------------------------------

    def get_allowed_domains(self, name: str) -> list[str] | None:
        return self._domains.get_allowed_domains(name)

    def get_proxy_blocked_log(self, name: str) -> str | None:
        return self._domains.get_proxy_blocked_log(name)

    def update_allowed_domains(self, name: str, domains: list[str]) -> None:
        self._domains.update_allowed_domains(name, domains)
