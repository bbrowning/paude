"""OpenShift backend configuration."""

from __future__ import annotations

from dataclasses import dataclass, field


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
