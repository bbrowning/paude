"""Test version information."""

import re

from paude import __version__


def test_version_format():
    """Verify version is a valid semver string."""
    # Match major.minor.patch with optional pre-release suffix
    pattern = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$"
    assert re.match(pattern, __version__), f"Invalid version format: {__version__}"
