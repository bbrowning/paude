"""Test version information."""

from paude import __version__


def test_version():
    """Verify version matches expected value."""
    assert __version__ == "0.4.0"
