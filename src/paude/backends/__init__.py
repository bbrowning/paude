"""Backend abstraction for paude container execution."""

from paude.backends.base import Backend, Session
from paude.backends.openshift import (
    NamespaceNotFoundError,
    OcNotInstalledError,
    OcNotLoggedInError,
    OcTimeoutError,
    OpenShiftBackend,
    OpenShiftConfig,
    OpenShiftError,
    RegistryNotAccessibleError,
)
from paude.backends.podman import PodmanBackend

__all__ = [
    "Backend",
    "NamespaceNotFoundError",
    "OcNotInstalledError",
    "OcNotLoggedInError",
    "OcTimeoutError",
    "OpenShiftBackend",
    "OpenShiftConfig",
    "OpenShiftError",
    "PodmanBackend",
    "RegistryNotAccessibleError",
    "Session",
]
