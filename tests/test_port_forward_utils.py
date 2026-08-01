"""Tests for port-forward spec parsing and label encoding utilities."""

from __future__ import annotations

import pytest

from paude.backends.port_forward_utils import (
    decode_forward_ports,
    encode_forward_ports,
    merge_forward_ports,
    parse_forward_port_spec,
    parse_forward_port_specs,
)


class TestParseForwardPortSpec:
    """Tests for parse_forward_port_spec (single spec)."""

    def test_bare_port_same_on_both(self):
        assert parse_forward_port_spec("8372") == ("127.0.0.1", 8372, 8372)

    def test_host_container(self):
        assert parse_forward_port_spec("8080:80") == ("127.0.0.1", 8080, 80)

    def test_host_ip_host_container(self):
        assert parse_forward_port_spec("0.0.0.0:8080:80") == ("0.0.0.0", 8080, 80)

    def test_strips_whitespace(self):
        assert parse_forward_port_spec("  8372  ") == ("127.0.0.1", 8372, 8372)

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            parse_forward_port_spec("   ")

    def test_non_integer_port_raises(self):
        with pytest.raises(ValueError, match="not an integer"):
            parse_forward_port_spec("abc")

    def test_port_out_of_range_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_forward_port_spec("70000")

    def test_zero_port_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            parse_forward_port_spec("0")

    def test_too_many_parts_raises(self):
        with pytest.raises(ValueError, match="expected"):
            parse_forward_port_spec("1:2:3:4")

    def test_empty_host_ip_raises(self):
        with pytest.raises(ValueError, match="empty host IP"):
            parse_forward_port_spec(":8080:80")

    def test_invalid_host_ip_raises(self):
        with pytest.raises(ValueError, match="not a valid IP address"):
            parse_forward_port_spec("999.999.999.999:8080:80")

    def test_hostname_as_host_ip_raises(self):
        with pytest.raises(ValueError, match="not a valid IP address"):
            parse_forward_port_spec("localhost:8080:80")


class TestParseForwardPortSpecs:
    """Tests for parse_forward_port_specs (list with de-duplication)."""

    def test_empty_list(self):
        assert parse_forward_port_specs([]) == []

    def test_multiple_specs(self):
        result = parse_forward_port_specs(["8372", "9090:90"])
        assert result == [("127.0.0.1", 8372, 8372), ("127.0.0.1", 9090, 90)]

    def test_exact_duplicates_collapsed(self):
        result = parse_forward_port_specs(["8372", "8372"])
        assert result == [("127.0.0.1", 8372, 8372)]

    def test_conflicting_host_bind_raises(self):
        with pytest.raises(ValueError, match="conflicting"):
            parse_forward_port_specs(["8080:80", "8080:81"])

    def test_same_host_port_different_ip_is_not_conflict(self):
        result = parse_forward_port_specs(["127.0.0.1:8080:80", "0.0.0.0:8080:81"])
        assert result == [("127.0.0.1", 8080, 80), ("0.0.0.0", 8080, 81)]


class TestEncodeDecodeForwardPorts:
    """Tests for label encode/decode round-tripping."""

    def test_round_trip(self):
        ports = [("127.0.0.1", 8372, 8372), ("0.0.0.0", 8080, 80)]
        encoded = encode_forward_ports(ports)
        assert encoded == "127.0.0.1:8372:8372,0.0.0.0:8080:80"
        assert decode_forward_ports(encoded) == ports

    def test_decode_empty_string(self):
        assert decode_forward_ports("") == []

    def test_decode_skips_malformed_entries(self):
        # Second entry is malformed and should be skipped, not raise.
        assert decode_forward_ports("127.0.0.1:8372:8372,garbage") == [
            ("127.0.0.1", 8372, 8372)
        ]

    def test_encode_empty(self):
        assert encode_forward_ports([]) == ""


class TestMergeForwardPorts:
    """Tests for merge_forward_ports (agent + user port merging)."""

    def test_user_and_agent_ports_both_kept(self):
        result = merge_forward_ports([("0.0.0.0", 8080, 80)], [(18789, 18789)])
        assert result == [("0.0.0.0", 8080, 80), ("127.0.0.1", 18789, 18789)]

    def test_user_port_wins_on_loopback_conflict(self):
        result = merge_forward_ports([("127.0.0.1", 8372, 9999)], [(8372, 8372)])
        assert result == [("127.0.0.1", 8372, 9999)]

    def test_agent_only_ports_default_to_loopback(self):
        result = merge_forward_ports([], [(18789, 18789)])
        assert result == [("127.0.0.1", 18789, 18789)]

    def test_empty_both_returns_empty(self):
        assert merge_forward_ports([], []) == []
