"""OpenShift backend package.

This module provides the OpenShift backend implementation for running
Claude Code in pods on an OpenShift cluster.

Public API:
    OpenShiftBackend: Main backend class for session management.
    OpenShiftConfig: Configuration dataclass for backend settings.
    OpenShiftError: Base exception for all OpenShift-related errors.

Exceptions:
    OcNotInstalledError: The oc CLI is not installed.
    OcNotLoggedInError: Not logged in to OpenShift cluster.
    OcTimeoutError: The oc CLI command timed out.
    BuildFailedError: OpenShift binary build failed.
    NamespaceNotFoundError: Namespace does not exist.
    SessionExistsError: Session with this name already exists.
    SessionNotFoundError: Session not found.
    PodNotFoundError: Pod not found.
    PodNotReadyError: Pod is not ready.

Utilities (for testing):
    _generate_session_name: Generate session name from workspace path.
    _encode_path: Base64 encode a path for labels.
    _decode_path: Decode a base64-encoded path.
    CLAUDE_EXCLUDES: List of patterns to exclude when syncing ~/.claude.
"""

from paude.backends.openshift.backend import OpenShiftBackend
from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import (
    BuildFailedError,
    NamespaceNotFoundError,
    OcNotInstalledError,
    OcNotLoggedInError,
    OcTimeoutError,
    OpenShiftError,
    PodNotFoundError,
    PodNotReadyError,
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.openshift.resources import (
    CLAUDE_EXCLUDES,
    _decode_path,
    _encode_path,
    _generate_session_name,
)

__all__ = [
    # Main classes
    "OpenShiftBackend",
    "OpenShiftConfig",
    # Exceptions
    "OpenShiftError",
    "OcNotInstalledError",
    "OcNotLoggedInError",
    "OcTimeoutError",
    "BuildFailedError",
    "NamespaceNotFoundError",
    "SessionExistsError",
    "SessionNotFoundError",
    "PodNotFoundError",
    "PodNotReadyError",
    # Utilities (for testing)
    "_generate_session_name",
    "_encode_path",
    "_decode_path",
    "CLAUDE_EXCLUDES",
]
