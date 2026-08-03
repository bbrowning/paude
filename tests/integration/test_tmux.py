"""Integration tests for the tmux package in the standard image."""

from __future__ import annotations

import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.podman]


def test_capture_pane_does_not_crash_tmux_server(
    require_podman: None,
    require_test_image: None,
    podman_test_image: str,
) -> None:
    """The distribution tmux package handles capture-pane safely."""
    smoke_command = """
set -eu
socket=paude-capture-pane-smoke
cleanup() {
    tmux -L "$socket" kill-server 2>/dev/null || true
}
trap cleanup EXIT
command -v tmux
rpm -q tmux
tmux -L "$socket" -f /dev/null new-session -d -s capture-test \
    "printf 'capture-pane smoke'; sleep 5"
tmux -L "$socket" capture-pane -p -t capture-test:0 >/tmp/capture-pane.out
grep -q 'capture-pane smoke' /tmp/capture-pane.out
tmux -L "$socket" has-session -t capture-test
"""
    result = subprocess.run(
        ["podman", "run", "--rm", podman_test_image, "bash", "-c", smoke_command],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        "tmux capture-pane smoke test failed:\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
