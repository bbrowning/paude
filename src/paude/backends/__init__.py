"""Backend abstraction for paude container execution."""

from paude.backends.base import Backend, LegacyBackend, Session, SessionConfig
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
from paude.backends.podman import (
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
)

__all__ = [
    "Backend",
    "LegacyBackend",
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
    "SessionConfig",
    "SessionExistsError",
    "SessionNotFoundError",
]
