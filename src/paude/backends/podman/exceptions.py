"""Podman backend exceptions."""

from paude.backends.base import SessionExistsError as _BaseExists
from paude.backends.base import SessionNotFoundError as _BaseNotFound


class SessionExistsError(_BaseExists):
    """Session already exists."""

    pass


class SessionNotFoundError(_BaseNotFound):
    """Session not found."""

    pass
