"""Helpers for assertions against terminal-rendered output."""

from __future__ import annotations

import re

_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI Select Graphic Rendition sequences from text."""
    return _ANSI_SGR.sub("", text)
