"""Tests for destination-scoped allowed endpoint validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from paude.endpoints import normalize_allowed_endpoints


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


def test_proxy_image_is_pinned_to_endpoint_capable_merge() -> None:
    dockerfile = (Path(__file__).parents[1] / "containers/proxy/Dockerfile").read_text()
    assert "598d2d89cbc6a9002db71de9d31d20d70fefb1cc" in dockerfile
