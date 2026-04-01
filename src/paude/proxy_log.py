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

    for line in raw_log.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[-1] != "BLOCKED":
            continue

        timestamp = f"{parts[0]} {parts[1]}"
        url = parts[5]
        domain = _extract_domain(url)
        if not domain:
            continue

        counts[domain] = counts.get(domain, 0) + 1
        last_seen[domain] = timestamp

    result = [
        BlockedDomain(domain=d, count=counts[d], last_seen=last_seen[d]) for d in counts
    ]
    result.sort(key=lambda b: b.count, reverse=True)
    return result


def _extract_domain(url: str) -> str | None:
    """Extract hostname from a URL or host:port string."""
    if "://" in url:
        parsed = urlparse(url)
        return parsed.hostname or None

    # CONNECT-style: host:port
    host = url.split(":")[0]
    return host if host else None
