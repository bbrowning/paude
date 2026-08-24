"""Tests for pre-upgrade migration of legacy container state."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call

import pytest

from paude.agents import get_agents
from paude.cli.upgrade_persistence import (
    _MIGRATE_SCRIPT,
    migrate_legacy_state,
    persistent_state_paths,
)


def test_composed_state_paths_include_agent_and_shared_state() -> None:
    composition = get_agents(
        ["codex", "cursor", "opencode", "gemini"], include_bundled=False
    )

    directories, files = persistent_state_paths(composition)

    assert directories == [
        ".codex",
        ".agents",
        ".cursor",
        ".config/cursor",
        ".config/opencode",
        ".local/share/opencode",
        ".local/state/opencode",
        ".gemini",
        ".dolt",
    ]
    assert files == [".gitconfig"]


def test_migration_starts_and_restops_a_stopped_container() -> None:
    runner = MagicMock()
    runner.container_running.return_value = False

    migrate_legacy_state(runner, "paude-demo", get_agents(["claude"]))

    runner.start_container.assert_called_once_with("paude-demo")
    runner.stop_container_graceful.assert_called_once_with("paude-demo")
    command = runner.exec_in_container.call_args.args[1]
    assert ".claude" in command
    assert ".claude.json" in command
    assert ".dolt" in command
    assert ".gitconfig" in command


def test_migration_quiesces_an_already_running_container() -> None:
    runner = MagicMock()
    runner.container_running.return_value = True

    migrate_legacy_state(runner, "paude-demo", get_agents(["codex"]))

    runner.stop_container_graceful.assert_called_once_with("paude-demo")
    runner.start_container.assert_called_once_with("paude-demo")
    assert runner.method_calls[:3] == [
        call.container_running("paude-demo"),
        call.stop_container_graceful("paude-demo"),
        call.start_container("paude-demo"),
    ]


def test_migration_does_not_reconcile_ownership() -> None:
    """Migration must NOT chown the volume: the old container may own it at a
    pre-pin UID, and reconciling to the pinned user here would remove its write
    access. Ownership is reconciled on the recreated container instead."""
    runner = MagicMock()
    runner.container_running.return_value = False

    migrate_legacy_state(runner, "paude-demo", get_agents(["codex"]))

    runner.reconcile_volume_ownership.assert_not_called()
    # The copy runs as the container's default user, not root.
    assert runner.exec_in_container.call_args.kwargs.get("user") is None


def test_migration_surfaces_stderr_on_failure() -> None:
    runner = MagicMock()
    runner.container_running.return_value = False
    runner.exec_in_container.side_effect = subprocess.CalledProcessError(
        1, ["bash"], stderr="cp: cannot create '/pvc/.codex': Permission denied"
    )

    with pytest.raises(RuntimeError, match="Permission denied"):
        migrate_legacy_state(runner, "paude-demo", get_agents(["codex"]))

    # The container is still re-stopped even when the copy fails.
    runner.stop_container_graceful.assert_called_once_with("paude-demo")


def test_migration_skips_salvage_when_container_wont_start(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An old container that can't even start (e.g. a stale network
    reference) must not block the upgrade — salvage is best-effort and
    everything already persisted to the session volume is unaffected."""
    runner = MagicMock()
    runner.container_running.return_value = False
    runner.start_container.side_effect = subprocess.CalledProcessError(
        125, ["podman", "start", "paude-demo"], stderr="no such network"
    )

    migrate_legacy_state(runner, "paude-demo", get_agents(["codex"]))

    runner.exec_in_container.assert_not_called()
    assert "skipping salvage" in capsys.readouterr().err


def test_migration_prints_nonfatal_warnings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = MagicMock()
    runner.container_running.return_value = False
    runner.exec_in_container.return_value = subprocess.CompletedProcess(
        args=["bash"],
        returncode=0,
        stdout="",
        stderr="migrate: could not fully migrate .codex\n",
    )

    migrate_legacy_state(runner, "paude-demo", get_agents(["codex"]))

    assert "could not fully migrate .codex" in capsys.readouterr().err


def test_migrate_script_guards_every_write() -> None:
    """Each cp/mkdir in the migration script must be non-fatal and warn.

    Guards the fix against regressing back to an unguarded ``set -e`` copy that
    aborts the whole upgrade on one un-writable path (the reported failure).
    """
    # A warn helper emits per-path failures to stderr (surfaced by the caller).
    assert "warn()" in _MIGRATE_SCRIPT
    assert ">&2" in _MIGRATE_SCRIPT
    for line in _MIGRATE_SCRIPT.splitlines():
        stripped = line.strip()
        if stripped.startswith(("cp ", "mkdir ")):
            assert "||" in stripped, f"unguarded write: {stripped}"
