"""Podman backend package.

Public API:
    PodmanBackend: Main backend class for session management.
    SessionExistsError: Session with this name already exists.
    SessionNotFoundError: Session not found.
    _generate_session_name: Generate session name from workspace path.
"""

from paude.backends.podman.backend import PodmanBackend
from paude.backends.podman.exceptions import (
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.podman.helpers import _generate_session_name

__all__ = [
    "PodmanBackend",
    "SessionExistsError",
    "SessionNotFoundError",
    "_generate_session_name",
]
