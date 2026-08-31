"""Tests for terminal-output assertion helpers."""

import pytest

from tests.ansi import strip_ansi


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("plain text", "plain text", id="plain"),
        pytest.param("\x1b[31merror\x1b[0m", "error", id="color"),
        pytest.param("-\x1b[1;36m-option\x1b[0m", "--option", id="split-option"),
    ],
)
def test_strip_ansi(text: str, expected: str) -> None:
    """ANSI styling is removed without changing the rendered text."""
    assert strip_ansi(text) == expected


def test_strip_ansi_is_idempotent() -> None:
    """Normalizing output more than once does not alter it further."""
    styled = "\x1b[1mimportant\x1b[0m"
    normalized = strip_ansi(styled)

    assert strip_ansi(normalized) == normalized
