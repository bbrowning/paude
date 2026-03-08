"""Tests for proxy log parsing."""

from __future__ import annotations

from paude.proxy_log import parse_blocked_log


class TestParseBlockedLog:
    """Tests for parse_blocked_log function."""

    def test_empty_log_returns_empty_list(self) -> None:
        assert parse_blocked_log("") == []

    def test_single_connect_line(self) -> None:
        log = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.example.com:443 BLOCKED"
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].domain == "evil.example.com"
        assert result[0].count == 1
        assert result[0].last_seen == "08/Mar/2026:14:23:45 +0000"

    def test_single_get_line(self) -> None:
        log = "08/Mar/2026:14:23:45 +0000 10.0.0.2 TCP_DENIED/403 GET http://evil.example.com/path BLOCKED"
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].domain == "evil.example.com"

    def test_aggregates_same_domain(self) -> None:
        log = (
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:01:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED\n"
            "08/Mar/2026:14:02:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT evil.com:443 BLOCKED"
        )
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].count == 3
        assert result[0].last_seen == "08/Mar/2026:14:02:00 +0000"

    def test_sorts_by_count_descending(self) -> None:
        log = (
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT rare.com:443 BLOCKED\n"
            "08/Mar/2026:14:01:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT frequent.com:443 BLOCKED\n"
            "08/Mar/2026:14:02:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT frequent.com:443 BLOCKED\n"
            "08/Mar/2026:14:03:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT frequent.com:443 BLOCKED"
        )
        result = parse_blocked_log(log)
        assert len(result) == 2
        assert result[0].domain == "frequent.com"
        assert result[0].count == 3
        assert result[1].domain == "rare.com"
        assert result[1].count == 1

    def test_multiple_domains(self) -> None:
        log = (
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT a.com:443 BLOCKED\n"
            "08/Mar/2026:14:01:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT b.com:443 BLOCKED\n"
            "08/Mar/2026:14:02:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT c.com:443 BLOCKED"
        )
        result = parse_blocked_log(log)
        assert len(result) == 3
        domains = {r.domain for r in result}
        assert domains == {"a.com", "b.com", "c.com"}

    def test_skips_malformed_lines(self) -> None:
        log = (
            "this is not a valid log line\n"
            "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT valid.com:443 BLOCKED\n"
            "short line\n"
        )
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].domain == "valid.com"

    def test_http_url_with_port(self) -> None:
        log = "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 GET http://example.com:8080/path BLOCKED"
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].domain == "example.com"

    def test_connect_without_port(self) -> None:
        log = "08/Mar/2026:14:00:00 +0000 10.0.0.2 TCP_DENIED/403 CONNECT example.com BLOCKED"
        result = parse_blocked_log(log)
        assert len(result) == 1
        assert result[0].domain == "example.com"
