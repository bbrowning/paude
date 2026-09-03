"""Parse proxy blocked-domain logs."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class BlockedDomain:
    """A domain that was blocked by the proxy, with request count."""

    domain: str
    count: int
    last_seen: str
    port: int | None = None


def parse_blocked_log(raw_log: str) -> list[BlockedDomain]:
    """Parse proxy blocked log into aggregated domain entries.

    Each log line has the format:
        <date> <timezone> <client-ip> <status/code> <method> <url> BLOCKED

    The URL field is either ``host:port`` (CONNECT) or ``http://host/path`` (GET).

    Returns:
        List of BlockedDomain sorted by count descending.
    """
    counts: dict[str, int] = {}
    last_seen: dict[str, str] = {}
    ports: dict[str, int | None] = {}

    for line in raw_log.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[-1] != "BLOCKED":
            continue

        timestamp = f"{parts[0]} {parts[1]}"
        url = parts[5]
        host, port = _extract_destination(url)
        domain = _display_destination(host, port)
        if not domain:
            continue

        counts[domain] = counts.get(domain, 0) + 1
        last_seen[domain] = timestamp
        ports[domain] = port

    result = [
        BlockedDomain(domain=d, count=counts[d], last_seen=last_seen[d], port=ports[d])
        for d in counts
    ]
    result.sort(key=lambda b: b.count, reverse=True)
    return result


def _extract_destination(url: str) -> tuple[str | None, int | None]:
    """Extract the host and explicit port from a proxy log destination."""
    if "://" in url:
        parsed = urlparse(url)
        try:
            return parsed.hostname or None, parsed.port
        except ValueError:
            return parsed.hostname or None, None

    parsed = urlparse(f"//{url}")
    try:
        return parsed.hostname or None, parsed.port
    except ValueError:
        return parsed.hostname or None, None


def _display_destination(host: str | None, port: int | None) -> str | None:
    """Retain nonstandard ports while keeping ordinary domain blocks compact."""
    if not host:
        return None
    if port is None or port in (80, 443):
        return host
    bracketed = f"[{host}]" if ":" in host else host
    return f"{bracketed}:{port}"
