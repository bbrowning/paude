"""OpenShift backend exceptions."""

from __future__ import annotations


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


class PodNotFoundError(OpenShiftError):
    """Pod not found."""

    pass


class PodNotReadyError(OpenShiftError):
    """Pod is not ready."""

    pass


class NamespaceNotFoundError(OpenShiftError):
    """Namespace does not exist."""

    pass


class BuildFailedError(OpenShiftError):
    """OpenShift binary build failed."""

    def __init__(
        self, build_name: str, reason: str, logs: str | None = None
    ) -> None:
        self.build_name = build_name
        self.reason = reason
        self.logs = logs
        message = f"Build '{build_name}' failed: {reason}"
        if logs:
            message += f"\n\nBuild logs:\n{logs}"
        super().__init__(message)


class SessionExistsError(OpenShiftError):
    """Session with this name already exists."""

    pass


class SessionNotFoundError(OpenShiftError):
    """Session not found."""

    pass
