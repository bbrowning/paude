"""Podman backend exceptions."""


class SessionExistsError(Exception):
    """Session already exists."""

    pass


class SessionNotFoundError(Exception):
    """Session not found."""

    pass
