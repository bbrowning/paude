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

    def test_active_recent(self) -> None:
        ts = str(int(time.time()) - 30)
        assert _detect_state(ts) == "Active"

    def test_idle_old(self) -> None:
        ts = str(int(time.time()) - 600)
        assert _detect_state(ts) == "Idle"

    def test_active_boundary(self) -> None:
        ts = str(int(time.time()) - 119)
        assert _detect_state(ts) == "Active"

    def test_idle_boundary(self) -> None:
        ts = str(int(time.time()) - 120)
        assert _detect_state(ts) == "Idle"

    def test_invalid_timestamp(self) -> None:
        assert _detect_state("not-a-number") == "Idle"

    def test_empty_string(self) -> None:
        assert _detect_state("") == "Idle"


class TestParseActivity:
    """Tests for parse_activity."""

    def test_returns_active(self) -> None:
        ts = str(int(time.time()) - 30)
        result = parse_activity(ts)
        assert isinstance(result, SessionActivity)
        assert result.state == "Active"

    def test_returns_idle(self) -> None:
        ts = str(int(time.time()) - 300)
        result = parse_activity(ts)
        assert result.state == "Idle"


class TestGetSessionActivity:
    """Tests for get_session_activity."""

    def test_queries_tmux(self) -> None:
        mock_backend = MagicMock()
        ts = str(int(time.time()) - 30)
        mock_backend.exec_in_session.return_value = (0, f"{ts}\n", "")

        result = get_session_activity(mock_backend, "my-session")

        assert result.state == "Active"
        assert mock_backend.exec_in_session.call_count == 1

    def test_handles_tmux_failure(self) -> None:
        mock_backend = MagicMock()
        mock_backend.exec_in_session.return_value = (1, "", "no tmux")

        result = get_session_activity(mock_backend, "my-session")

        assert result.last_activity == "unknown"
        assert result.state == "Idle"
