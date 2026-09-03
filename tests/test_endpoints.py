"""Tests for destination-scoped allowed endpoint validation."""

from __future__ import annotations

import pytest

from paude.domains import host_matches_allowed_domains
from paude.endpoints import (
    normalize_allowed_endpoints,
    warn_for_uncovered_allowed_endpoints,
)


def test_normalizes_and_deduplicates_exact_authorities() -> None:
    assert normalize_allowed_endpoints(
        [
            "Example.COM.:08000,[2001:0db8::1]:4317",
            "[::ffff:192.0.2.1]:9000",
            "example.com:8000",
            "API_SERVICE:8443",
        ]
    ) == [
        "example.com:8000",
        "[2001:db8::1]:4317",
        "192.0.2.1:9000",
        "api_service:8443",
    ]


@pytest.mark.parametrize(
    "value",
    [
        "example.com",
        "example.com:",
        "example.com:0",
        "example.com:65536",
        "example.com:+80",
        "http://example.com:8000",
        "user@example.com:8000",
        "example.com:8000/path",
        ".example.com:8000",
        "~example:8000",
        "*.example.com:8000",
        "[example.com]:8000",
        "[fe80::1%eth0]:8000",
        "2001:db8::1:8000",
        "192.168.001.001:8000",
    ],
)
def test_rejects_non_exact_or_malformed_authorities(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid allowed endpoint"):
        normalize_allowed_endpoints([value])


@pytest.mark.parametrize(
    ("host", "domains"),
    [
        ("api.example.com", ["api.example.com"]),
        ("api.example.com", [".example.com"]),
        ("us-east5-aiplatform.googleapis.com", ["vertexai"]),
        ("anything.example", []),
        ("anything.example", ["all"]),
    ],
)
def test_allowed_domain_forms_cover_endpoint_hosts(
    host: str, domains: list[str]
) -> None:
    assert host_matches_allowed_domains(host, domains)


def test_warns_for_each_endpoint_without_domain_coverage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    warn_for_uncovered_allowed_endpoints(
        ["api.example.com:8443", "other.example:9000"],
        ["covered.example"],
        session_name="demo",
    )

    stderr = capsys.readouterr().err
    assert "allowed endpoint 'api.example.com:8443' will remain blocked" in stderr
    assert "allowed endpoint 'other.example:9000' will remain blocked" in stderr
    assert "paude allowed-domains demo --add api.example.com" in stderr
