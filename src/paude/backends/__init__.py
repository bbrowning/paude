"""Backend abstraction for paude container execution."""

from paude.backends.base import (
    Backend,
    Session,
    SessionConfig,
    SessionExistsError,
    SessionNotFoundError,
)
from paude.backends.podman import PodmanBackend

__all__ = [
    "Backend",
    "PodmanBackend",
    "Session",
    "SessionConfig",
    "SessionExistsError",
    "SessionNotFoundError",
]
