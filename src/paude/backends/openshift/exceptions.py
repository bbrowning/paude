"""OpenShift backend exceptions."""

from __future__ import annotations

from paude.backends.base import SessionExistsError as _BaseExists
from paude.backends.base import SessionNotFoundError as _BaseNotFound


class OpenShiftError(Exception):
    """Base exception for OpenShift backend errors."""

    pass


class OcNotInstalledError(OpenShiftError):
    """The oc CLI is not installed."""

    pass


class OcNotLoggedInError(OpenShiftError):
    """Not logged in to OpenShift cluster."""

    pass


class OcTimeoutError(OpenShiftError):
    """The oc CLI command timed out."""

    pass


class PodNotReadyError(OpenShiftError):
    """Pod is not ready."""

    pass


class NamespaceNotFoundError(OpenShiftError):
    """Namespace does not exist."""

    pass


class BuildFailedError(OpenShiftError):
    """OpenShift binary build failed."""

    def __init__(self, build_name: str, reason: str, logs: str | None = None) -> None:
        self.build_name = build_name
        self.reason = reason
        self.logs = logs
        message = f"Build '{build_name}' failed: {reason}"
        if logs:
            message += f"\n\nBuild logs:\n{logs}"
        super().__init__(message)


class SessionExistsError(OpenShiftError, _BaseExists):
    """Session with this name already exists."""

    pass


class SessionNotFoundError(OpenShiftError, _BaseNotFound):
    """Session not found."""

    pass
