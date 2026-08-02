"""Tests for pre-upgrade migration of legacy container state."""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from paude.agents import get_agents
from paude.cli.upgrade_persistence import (
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


def test_migration_restops_container_when_copy_fails() -> None:
    runner = MagicMock()
    runner.container_running.return_value = False
    runner.exec_in_container.side_effect = RuntimeError("copy failed")

    with pytest.raises(RuntimeError, match="copy failed"):
        migrate_legacy_state(runner, "paude-demo", get_agents(["opencode"]))

    runner.stop_container_graceful.assert_called_once_with("paude-demo")
