"""Session lookup and query operations for OpenShift backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paude.backends.base import Session
from paude.backends.openshift.exceptions import SessionNotFoundError
from paude.backends.openshift.oc import OcClient
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_APP,
    PAUDE_LABEL_SESSION,
    PAUDE_LABEL_VERSION,
    decode_path,
    pod_name,
    proxy_resource_name,
    pvc_name,
    resource_name,
)


class SessionLookup:
    """Handles session lookup and query operations.

    This class provides read-only operations to find, list, and inspect
    sessions on an OpenShift cluster.
    """

    def __init__(self, oc: OcClient, namespace: str) -> None:
        self._oc = oc
        self._namespace = namespace

    def get_statefulset(self, session_name: str) -> dict[str, Any] | None:
        """Get StatefulSet for a session.

        Returns:
            StatefulSet data or None if not found.
        """
        sts_name = resource_name(session_name)

        result = self._oc.run(
            "get",
            "statefulset",
            sts_name,
            "-n",
            self._namespace,
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

    def require_session(self, name: str) -> dict[str, Any]:
        """Get StatefulSet for a session, raising if not found.

        Raises:
            SessionNotFoundError: If session not found.
        """
        sts = self.get_statefulset(name)
        if sts is None:
            raise SessionNotFoundError(f"Session '{name}' not found")
        return sts

    def get_pod_for_session(self, session_name: str) -> str | None:
        """Get the pod name for a session if it exists.

        Returns:
            Pod name or None if not found.
        """
        pname = pod_name(session_name)

        result = self._oc.run(
            "get",
            "pod",
            pname,
            "-n",
            self._namespace,
            "-o",
            "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0:
            return None

        return pname

    def require_running_pod(self, name: str) -> str:
        """Get pod name for a session, raising if not found or not running.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        self.require_session(name)
        pname = self.get_pod_for_session(name)
        if pname is None:
            raise ValueError(
                f"Session '{name}' is not running. "
                f"Use 'paude start {name}' to start it."
            )
        return pname

    def has_proxy_deployment(self, session_name: str) -> bool:
        """Check if a proxy deployment exists for a session."""
        result = self._oc.run(
            "get",
            "deployment",
            proxy_resource_name(session_name),
            "-n",
            self._namespace,
            check=False,
        )
        return result.returncode == 0

    def session_from_statefulset(
        self, sts: dict[str, Any], name: str | None = None
    ) -> Session:
        """Build a Session object from a StatefulSet dict."""
        metadata = sts.get("metadata", {})
        labels = metadata.get("labels", {})
        annotations = metadata.get("annotations", {})
        spec = sts.get("spec", {})

        session_name = name or labels.get(PAUDE_LABEL_SESSION, "unknown")

        replicas = spec.get("replicas", 0)
        ready_replicas = sts.get("status", {}).get("readyReplicas", 0)

        if replicas == 0:
            status = "stopped"
        elif ready_replicas > 0:
            status = "running"
        else:
            status = "pending"

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
            version=labels.get(PAUDE_LABEL_VERSION),
        )

    def get_session(self, name: str) -> Session | None:
        """Get a session by name."""
        sts = self.get_statefulset(name)
        if sts is None:
            return None
        return self.session_from_statefulset(sts, name=name)

    def find_session_for_workspace(self, workspace: Path) -> Session | None:
        """Find an existing session for the given workspace."""
        sessions = self.list_sessions()
        workspace_resolved = workspace.resolve()

        for session in sessions:
            if session.workspace.resolve() == workspace_resolved:
                return session

        return None

    def list_sessions(self) -> list[Session]:
        """List all sessions (StatefulSets)."""
        result = self._oc.run(
            "get",
            "statefulsets",
            "-n",
            self._namespace,
            "-l",
            PAUDE_LABEL_APP,
            "-o",
            "json",
            check=False,
        )

        if result.returncode != 0:
            return []

        try:
            data = json.loads(result.stdout)
            return [
                self.session_from_statefulset(item) for item in data.get("items", [])
            ]
        except json.JSONDecodeError:
            return []
