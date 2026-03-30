"""OpenTelemetry configuration helpers for paude agents."""

from __future__ import annotations

from urllib.parse import urlparse

# Ports already allowed by squid.conf (no need to inject)
_SQUID_DEFAULT_PORTS = {80, 443}

# Default OTLP HTTP port
_DEFAULT_OTLP_PORT = 4318


def build_otel_env(agent_name: str, endpoint: str) -> dict[str, str]:
    """Build agent-specific OTEL environment variables.

    Each agent uses different env vars for OTEL configuration.
    All are configured to use http/protobuf protocol for maximum
    compatibility.

    Args:
        agent_name: Agent name (claude, gemini, openclaw, cursor).
        endpoint: OTLP collector endpoint URL.

    Returns:
        Dictionary of environment variables to set in the container.
    """
    builders = {
        "claude": _build_claude_otel_env,
        "gemini": _build_gemini_otel_env,
        "openclaw": _build_openclaw_otel_env,
    }
    builder = builders.get(agent_name)
    if builder is None:
        return {}
    return builder(endpoint)


def _build_claude_otel_env(endpoint: str) -> dict[str, str]:
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
        "OTEL_METRIC_EXPORT_INTERVAL": "30000",
    }


def _build_gemini_otel_env(endpoint: str) -> dict[str, str]:
    # Gemini CLI reads GEMINI_TELEMETRY_* env vars in
    # packages/core/src/telemetry/config.ts:resolveTelemetrySettings().
    # It also checks OTEL_EXPORTER_OTLP_ENDPOINT as a fallback for the
    # endpoint, so we set both for robustness.
    #
    # Protocol "http" = HTTP/protobuf (same as "http/protobuf" in the
    # standard OTEL SDK naming).  Valid values: "grpc" | "http".
    return {
        "GEMINI_TELEMETRY_ENABLED": "1",
        "GEMINI_TELEMETRY_OTLP_ENDPOINT": endpoint,
        "GEMINI_TELEMETRY_OTLP_PROTOCOL": "http",
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    }


def _build_openclaw_otel_env(endpoint: str) -> dict[str, str]:
    return {
        "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_LOG_LEVEL": "debug",
    }


def _all_otel_env_keys() -> frozenset[str]:
    """Compute union of all env var keys that build_otel_env may set."""
    dummy = "http://dummy:4318"
    keys: set[str] = set()
    builders = (
        _build_claude_otel_env,
        _build_gemini_otel_env,
        _build_openclaw_otel_env,
    )
    for builder in builders:
        keys.update(builder(dummy))
    return frozenset(keys)


# All env var keys that build_otel_env may set (union across all agents).
# Used to clear OTEL configuration when removing --otel-endpoint.
OTEL_ENV_KEYS: frozenset[str] = _all_otel_env_keys()


def parse_otel_endpoint(endpoint: str) -> tuple[str, int]:
    """Extract hostname and port from an OTEL endpoint URL.

    Handles formats:
    - http://collector.example.com:4318
    - https://collector.example.com
    - collector.example.com:4318
    - collector.example.com (defaults to port 4318)

    Args:
        endpoint: OTEL endpoint URL or host:port string.

    Returns:
        Tuple of (hostname, port).
    """
    parsed = urlparse(endpoint)

    if parsed.hostname:
        # Full URL with scheme
        port = parsed.port or _DEFAULT_OTLP_PORT
        return parsed.hostname, port

    # No scheme — try adding one for urlparse
    parsed = urlparse(f"http://{endpoint}")
    if parsed.hostname:
        port = parsed.port or _DEFAULT_OTLP_PORT
        return parsed.hostname, port

    # Fallback: treat entire string as hostname
    return endpoint, _DEFAULT_OTLP_PORT


def otel_proxy_ports(endpoint: str) -> list[int]:
    """Return ports that must be opened in squid for the OTEL endpoint.

    Filters out ports already allowed by default (80, 443).

    Args:
        endpoint: OTEL endpoint URL.

    Returns:
        List of ports to add to squid Safe_ports/SSL_ports ACLs.
    """
    _, port = parse_otel_endpoint(endpoint)
    if port in _SQUID_DEFAULT_PORTS:
        return []
    return [port]
