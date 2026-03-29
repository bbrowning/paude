"""Kubernetes resource builders and utilities for OpenShift backend."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_PROVIDER,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_YOLO,
    encode_path,
    resource_name,
)
from paude.constants import CONTAINER_WORKSPACE


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
        agent: str = "claude",
        provider: str | None = None,
        gpu: str | None = None,
        yolo: bool = False,
    ) -> None:
        """Initialize the StatefulSet builder.

        Args:
            session_name: Session name.
            namespace: Kubernetes namespace.
            image: Container image to use.
            resources: Resource requests/limits for the container.
            agent: Agent name (e.g., "claude").
            provider: Inference provider name (e.g., "vertex", "openai").
            gpu: GPU spec (e.g., "all", "device=0,1", "2").
            yolo: Whether YOLO mode is enabled.
        """
        self._session_name = session_name
        self._namespace = namespace
        self._image = image
        self._resources = resources
        self._agent = agent
        self._provider = provider
        self._gpu = gpu
        self._yolo = yolo
        self._otel_endpoint: str | None = None
        self._env: dict[str, str] = {}
        self._workspace: Path | None = None
        self._pvc_size = "10Gi"
        self._storage_class: str | None = None

    def with_otel_endpoint(self, endpoint: str | None) -> StatefulSetBuilder:
        """Set the OTEL endpoint (for annotation).

        Args:
            endpoint: OTLP collector endpoint URL.

        Returns:
            Self for method chaining.
        """
        self._otel_endpoint = endpoint
        return self

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
        from paude import __version__

        sts_name = resource_name(self._session_name)
        labels: dict[str, str] = {
            "app": "paude",
            "paude.io/session-name": self._session_name,
            PAUDE_LABEL_AGENT: self._agent,
            PAUDE_LABEL_VERSION: __version__,
        }
        if self._provider:
            labels[PAUDE_LABEL_PROVIDER] = self._provider
        if self._gpu:
            labels[PAUDE_LABEL_GPU] = self._gpu
        if self._yolo:
            labels[PAUDE_LABEL_YOLO] = "1"

        metadata: dict[str, Any] = {
            "name": sts_name,
            "namespace": self._namespace,
            "labels": labels,
            "annotations": {
                "paude.io/created-at": created_at,
            },
        }
        if self._workspace:
            encoded = encode_path(self._workspace)
            metadata["annotations"]["paude.io/workspace"] = encoded
        if self._otel_endpoint:
            metadata["annotations"]["paude.io/otel-endpoint"] = self._otel_endpoint
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

    def _parse_gpu_count(self) -> str | None:
        """Parse GPU spec into a resource count for Kubernetes.

        Returns:
            GPU count string (e.g., "1", "2") or None if no GPU.
        """
        if not self._gpu:
            return None
        if self._gpu == "all":
            return "1"
        if self._gpu.startswith("device="):
            devices = self._gpu[len("device=") :]
            return str(len(devices.split(",")))
        # Treat as a plain numeric count
        return self._gpu

    def _build_container_spec(self) -> dict[str, Any]:
        """Build the container spec for the pod template."""
        env_list = [{"name": k, "value": v} for k, v in self._env.items()]
        env_list.append({"name": "PAUDE_WORKSPACE", "value": CONTAINER_WORKSPACE})

        # Allow override for testing with locally-loaded images in Kind
        image_pull_policy = os.environ.get("PAUDE_IMAGE_PULL_POLICY", "Always")

        resources = dict(self._resources)
        gpu_count = self._parse_gpu_count()
        if gpu_count:
            resources = {
                k: {**v, "nvidia.com/gpu": gpu_count} for k, v in resources.items()
            }

        return {
            "name": "paude",
            "image": self._image,
            "imagePullPolicy": image_pull_policy,
            "command": ["tini", "--", "sleep", "infinity"],
            "stdin": True,
            "tty": True,
            "env": env_list,
            "resources": resources,
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
        sts_name = resource_name(self._session_name)
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
