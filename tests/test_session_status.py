"""Tests for the session_status module."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

from paude.session_status import (
    SessionActivity,
    _detect_state,
    _format_elapsed,
    get_session_activity,
    parse_activity,
)


class TestFormatElapsed:
    """Tests for _format_elapsed."""

    def test_seconds_ago(self) -> None:
        ts = str(int(time.time()) - 30)
        result = _format_elapsed(ts)
        assert result == "30s ago"

    def test_minutes_ago(self) -> None:
        ts = str(int(time.time()) - 300)
        result = _format_elapsed(ts)
        assert result == "5m ago"

    def test_hours_ago(self) -> None:
        ts = str(int(time.time()) - 7200)
        result = _format_elapsed(ts)
        assert result == "2h ago"

    def test_days_ago(self) -> None:
        ts = str(int(time.time()) - 172800)
        result = _format_elapsed(ts)
        assert result == "2d ago"

    def test_just_now(self) -> None:
        ts = str(int(time.time()) + 10)
        result = _format_elapsed(ts)
        assert result == "just now"

    def test_invalid_timestamp(self) -> None:
        result = _format_elapsed("not-a-number")
        assert result == "unknown"

    def test_empty_string(self) -> None:
        result = _format_elapsed("")
        assert result == "unknown"

    def test_multiline_takes_first(self) -> None:
        ts = str(int(time.time()) - 60)
        result = _format_elapsed(f"{ts}\nextra")
        assert result == "1m ago"


class TestDetectState:
    """Tests for _detect_state."""

    def test_waiting_for_input_approve(self) -> None:
        result = _detect_state("0", "Do you approve this plan?")
        assert result == "Waiting for input"

    def test_waiting_for_input_yn(self) -> None:
        result = _detect_state("0", "Continue? [Y/n]")
        assert result == "Waiting for input"

    def test_idle_at_prompt(self) -> None:
        result = _detect_state("0", "some output\n$ ")
        assert result == "Idle"

    def test_idle_at_chevron(self) -> None:
        result = _detect_state("0", "some output\n>")
        assert result == "Idle"

    def test_working_recent_activity(self) -> None:
        ts = str(int(time.time()) - 30)
        result = _detect_state(ts, "Running tests...\ntest_foo PASSED")
        assert result == "Working"

    def test_idle_old_activity(self) -> None:
        ts = str(int(time.time()) - 600)
        result = _detect_state(ts, "Running tests...\ntest_foo PASSED")
        assert result == "Idle"

    def test_empty_content(self) -> None:
        result = _detect_state("0", "")
        assert result == "Idle"

    def test_waiting_permission(self) -> None:
        result = _detect_state("0", "Grant permission to run command?")
        assert result == "Waiting for input"


class TestParseActivity:
    """Tests for parse_activity."""

    def test_returns_session_activity(self) -> None:
        ts = str(int(time.time()) - 120)
        result = parse_activity(ts, "output\n>")
        assert isinstance(result, SessionActivity)
        assert result.state == "Idle"
        assert "2m ago" in result.last_activity


class TestGetSessionActivity:
    """Tests for get_session_activity."""

    def test_queries_tmux(self) -> None:
        mock_backend = MagicMock()
        ts = str(int(time.time()) - 30)
        mock_backend.exec_in_session.side_effect = [
            (0, f"{ts}\n", ""),
            (0, "Working on task...\ntest PASSED", ""),
        ]

        result = get_session_activity(mock_backend, "my-session")

        assert result.state == "Working"
        assert mock_backend.exec_in_session.call_count == 2

    def test_handles_tmux_failure(self) -> None:
        mock_backend = MagicMock()
        mock_backend.exec_in_session.side_effect = [
            (1, "", "no tmux"),
            (1, "", "no tmux"),
        ]

        result = get_session_activity(mock_backend, "my-session")

        assert result.last_activity == "unknown"
        assert result.state == "Idle"
