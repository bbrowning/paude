"""Validation and canonicalization for destination-scoped proxy endpoints."""

from __future__ import annotations

import ipaddress
import re
import sys

from paude.domains import host_matches_allowed_domains

_HOST_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")


def normalize_allowed_endpoints(values: list[str] | None) -> list[str]:
    """Return canonical, de-duplicated exact ``host:port`` authorities.

    Values may be repeated or comma-separated. Invalid entries raise a
    user-facing ``ValueError`` before a proxy container is changed.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        for raw in value.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                endpoint = _canonical_authority(raw)
            except ValueError as exc:
                raise ValueError(f"Invalid allowed endpoint {raw!r}: {exc}") from None
            if endpoint not in seen:
                normalized.append(endpoint)
                seen.add(endpoint)
    return normalized


def warn_for_uncovered_allowed_endpoints(
    endpoints: list[str] | None,
    allowed_domains: list[str],
    *,
    session_name: str | None = None,
) -> None:
    """Warn about endpoint rules whose hosts remain domain-blocked."""
    for endpoint in normalize_allowed_endpoints(endpoints):
        host = _authority_host(endpoint)
        if host_matches_allowed_domains(host, allowed_domains):
            continue
        if session_name:
            action = (
                f"Run 'paude allowed-domains {session_name} --add {host}' to enable it."
            )
        else:
            action = f"Add '--allowed-domains {host}' to enable it."
        print(
            f"Warning: allowed endpoint '{endpoint}' will remain blocked because "
            f"host '{host}' is not allowed by allowed-domains. {action}",
            file=sys.stderr,
        )


def _authority_host(authority: str) -> str:
    """Extract the canonical host from a normalized authority."""
    if authority.startswith("["):
        return authority[1 : authority.index("]")]
    return authority.rsplit(":", 1)[0]


def _canonical_authority(authority: str) -> str:
    """Canonicalize one exact authority using the proxy's accepted grammar."""
    if any(char in authority for char in "/?#@") or "://" in authority:
        raise ValueError(
            "must be host:port without a scheme, path, query, fragment, or userinfo"
        )

    if authority.startswith("["):
        close = authority.find("]")
        if close < 0 or close + 1 >= len(authority) or authority[close + 1] != ":":
            raise ValueError("must be an exact host:port authority")
        host = authority[1:close]
        port_text = authority[close + 2 :]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("brackets are only valid around IPv6 addresses") from None
        if not isinstance(address, ipaddress.IPv6Address):
            raise ValueError("brackets are only valid around IPv6 addresses")
    else:
        if authority.count(":") != 1:
            if authority.count(":") > 1:
                raise ValueError("IPv6 addresses must be bracketed")
            raise ValueError("must be an exact host:port authority")
        host, port_text = authority.rsplit(":", 1)

    if not port_text:
        raise ValueError("port is required")
    if not port_text.isascii() or not port_text.isdigit():
        raise ValueError(f"port {port_text!r} must be numeric")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError(f"port {port_text!r} must be between 1 and 65535")

    canonical_host = _canonical_host(host)
    if ":" in canonical_host:
        return f"[{canonical_host}]:{port}"
    return f"{canonical_host}:{port}"


def _canonical_host(host: str) -> str:
    """Canonicalize an exact hostname or IP address."""
    host = host.lower().removesuffix(".")
    if not host:
        raise ValueError("host is required")
    if host.startswith((".", "~")) or "*" in host:
        raise ValueError("host must be an exact hostname or IP address")
    if "%" in host:
        raise ValueError("IPv6 zones are not valid destination hosts")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return address.compressed

    if ":" in host:
        raise ValueError("IPv6 addresses must be bracketed")
    if all(char.isdigit() or char == "." for char in host):
        raise ValueError("invalid IP address")
    if len(host) > 253:
        raise ValueError("hostname is too long")
    if any(not _HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise ValueError("invalid hostname")
    return host
