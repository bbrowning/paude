"""Kubernetes resource builders and utilities for OpenShift backend."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


class StatefulSetBuilder:
    """Builder for Kubernetes StatefulSet specifications.

    Constructs StatefulSet specs for paude session pods with proper
    volume configuration and security settings.
    """

    def __init__(
        self,
        session_name: str,
        namespace: str,
        image: str,
        resources: dict[str, dict[str, str]],
    ) -> None:
        """Initialize the StatefulSet builder.

        Args:
            session_name: Session name.
            namespace: Kubernetes namespace.
            image: Container image to use.
            resources: Resource requests/limits for the container.
        """
        self._session_name = session_name
        self._namespace = namespace
        self._image = image
        self._resources = resources
        self._env: dict[str, str] = {}
        self._workspace: Path | None = None
        self._pvc_size = "10Gi"
        self._storage_class: str | None = None

    def with_env(self, env: dict[str, str]) -> StatefulSetBuilder:
        """Set environment variables for the container.

        Args:
            env: Dictionary of environment variables.

        Returns:
            Self for method chaining.
        """
        self._env = env
        return self

    def with_workspace(self, workspace: Path) -> StatefulSetBuilder:
        """Set the workspace path (for annotation).

        Args:
            workspace: Local workspace path.

        Returns:
            Self for method chaining.
        """
        self._workspace = workspace
        return self

    def with_pvc(
        self,
        size: str = "10Gi",
        storage_class: str | None = None,
    ) -> StatefulSetBuilder:
        """Configure the PVC for workspace storage.

        Args:
            size: Size of the PVC (e.g., "10Gi").
            storage_class: Storage class name (None for default).

        Returns:
            Self for method chaining.
        """
        self._pvc_size = size
        self._storage_class = storage_class
        return self

    def _build_metadata(self, created_at: str) -> dict[str, Any]:
        """Build the metadata section of the StatefulSet spec."""
        sts_name = f"paude-{self._session_name}"
        metadata: dict[str, Any] = {
            "name": sts_name,
            "namespace": self._namespace,
            "labels": {
                "app": "paude",
                "paude.io/session-name": self._session_name,
            },
            "annotations": {
                "paude.io/created-at": created_at,
            },
        }
        if self._workspace:
            encoded = _encode_path(self._workspace)
            metadata["annotations"]["paude.io/workspace"] = encoded
        return metadata

    def _build_volumes(self) -> list[dict[str, Any]]:
        """Build the volumes list for the pod spec."""
        return [
            {
                "name": "credentials",
                "emptyDir": {
                    "medium": "Memory",
                    "sizeLimit": "100Mi",
                },
            },
        ]

    def _build_volume_mounts(self) -> list[dict[str, Any]]:
        """Build the volume mounts list for the container spec."""
        return [
            {
                "name": "workspace",
                "mountPath": "/pvc",
            },
            {
                "name": "credentials",
                "mountPath": "/credentials",
            },
        ]

    def _build_container_spec(self) -> dict[str, Any]:
        """Build the container spec for the pod template."""
        env_list = [{"name": k, "value": v} for k, v in self._env.items()]
        env_list.append({"name": "PAUDE_WORKSPACE", "value": "/pvc/workspace"})

        return {
            "name": "paude",
            "image": self._image,
            "imagePullPolicy": "Always",
            "command": ["/usr/local/bin/entrypoint-session.sh"],
            "stdin": True,
            "tty": True,
            "env": env_list,
            "resources": self._resources,
            "volumeMounts": self._build_volume_mounts(),
        }

    def _build_pvc_spec(self) -> dict[str, Any]:
        """Build the PVC spec for volumeClaimTemplates."""
        pvc_spec: dict[str, Any] = {
            "accessModes": ["ReadWriteOnce"],
            "resources": {
                "requests": {
                    "storage": self._pvc_size,
                },
            },
        }
        if self._storage_class:
            pvc_spec["storageClassName"] = self._storage_class
        return pvc_spec

    def build(self) -> dict[str, Any]:
        """Build the complete StatefulSet specification.

        Returns:
            StatefulSet spec as a dictionary.
        """
        sts_name = f"paude-{self._session_name}"
        created_at = datetime.now(UTC).isoformat()

        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": self._build_metadata(created_at),
            "spec": {
                "replicas": 1,
                "serviceName": sts_name,
                "selector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": self._session_name,
                    },
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "paude",
                            "paude.io/session-name": self._session_name,
                        },
                    },
                    "spec": {
                        "containers": [self._build_container_spec()],
                        "volumes": self._build_volumes(),
                        "restartPolicy": "Always",
                    },
                },
                "volumeClaimTemplates": [
                    {
                        "metadata": {
                            "name": "workspace",
                        },
                        "spec": self._build_pvc_spec(),
                    },
                ],
            },
        }
