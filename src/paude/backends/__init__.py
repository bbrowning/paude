"""Backend abstraction for paude container execution."""

from paude.backends.base import Backend, Session, SessionConfig
from paude.backends.openshift import (
    BuildFailedError,
    NamespaceNotFoundError,
    OcNotInstalledError,
    OcNotLoggedInError,
    OcTimeoutError,
    OpenShiftBackend,
    OpenShiftConfig,
    OpenShiftError,
)
from paude.backends.podman import (
    PodmanBackend,
    SessionExistsError,
    SessionNotFoundError,
)

__all__ = [
    "Backend",
    "BuildFailedError",
    "NamespaceNotFoundError",
    "OcNotInstalledError",
    "OcNotLoggedInError",
    "OcTimeoutError",
    "OpenShiftBackend",
    "OpenShiftConfig",
    "OpenShiftError",
    "PodmanBackend",
    "Session",
    "SessionConfig",
    "SessionExistsError",
    "SessionNotFoundError",
]
