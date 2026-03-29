"""Tests for OpenTelemetry configuration helpers."""

from __future__ import annotations

from paude.otel import build_otel_env, otel_proxy_ports, parse_otel_endpoint


class TestBuildOtelEnv:
    """Tests for build_otel_env."""

    def test_claude_env(self):
        """Claude Code gets all required OTEL env vars."""
        env = build_otel_env("claude", "http://collector:4318")
        assert env["CLAUDE_CODE_ENABLE_TELEMETRY"] == "1"
        assert env["OTEL_METRICS_EXPORTER"] == "otlp"
        assert env["OTEL_LOGS_EXPORTER"] == "otlp"
        assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"

    def test_gemini_env(self):
        """Gemini CLI gets its specific OTEL env vars."""
        env = build_otel_env("gemini", "http://collector:4318")
        assert env["GEMINI_TELEMETRY_ENABLED"] == "true"
        assert env["GEMINI_TELEMETRY_TARGET"] == "local"
        assert env["GEMINI_TELEMETRY_USE_COLLECTOR"] == "true"
        assert env["GEMINI_TELEMETRY_OTLP_ENDPOINT"] == "http://collector:4318"
        assert env["GEMINI_TELEMETRY_OTLP_PROTOCOL"] == "http"

    def test_openclaw_env(self):
        """OpenClaw gets standard OTEL env vars."""
        env = build_otel_env("openclaw", "http://collector:4318")
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"
        assert env["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"

    def test_cursor_returns_empty(self):
        """Cursor has no OTEL support, returns empty dict."""
        env = build_otel_env("cursor", "http://collector:4318")
        assert env == {}

    def test_unknown_agent_returns_empty(self):
        """Unknown agents return empty dict."""
        env = build_otel_env("nonexistent", "http://collector:4318")
        assert env == {}

    def test_endpoint_passed_through(self):
        """Endpoint URL is passed through exactly as given."""
        url = "https://otel.example.com:443/v1/traces"
        env = build_otel_env("claude", url)
        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == url


class TestParseOtelEndpoint:
    """Tests for parse_otel_endpoint."""

    def test_full_http_url(self):
        """Parses http://host:port."""
        host, port = parse_otel_endpoint("http://collector:4318")
        assert host == "collector"
        assert port == 4318

    def test_full_https_url(self):
        """Parses https://host:port."""
        host, port = parse_otel_endpoint("https://otel.example.com:443")
        assert host == "otel.example.com"
        assert port == 443

    def test_http_url_no_port(self):
        """Defaults to port 4318 when no port specified."""
        host, port = parse_otel_endpoint("http://collector.example.com")
        assert host == "collector.example.com"
        assert port == 4318

    def test_bare_host_with_port(self):
        """Parses host:port without scheme."""
        host, port = parse_otel_endpoint("collector:4318")
        assert host == "collector"
        assert port == 4318

    def test_bare_host_no_port(self):
        """Bare hostname defaults to port 4318."""
        host, port = parse_otel_endpoint("collector.example.com")
        assert host == "collector.example.com"
        assert port == 4318

    def test_url_with_path(self):
        """URL with path extracts host and port correctly."""
        host, port = parse_otel_endpoint("http://collector:4318/v1/traces")
        assert host == "collector"
        assert port == 4318

    def test_localhost(self):
        """Handles localhost correctly."""
        host, port = parse_otel_endpoint("http://localhost:4318")
        assert host == "localhost"
        assert port == 4318


class TestOtelProxyPorts:
    """Tests for otel_proxy_ports."""

    def test_non_standard_port(self):
        """Non-standard port is returned for squid injection."""
        ports = otel_proxy_ports("http://collector:4318")
        assert ports == [4318]

    def test_port_443_excluded(self):
        """Port 443 is already allowed, not returned."""
        ports = otel_proxy_ports("https://collector:443")
        assert ports == []

    def test_port_80_excluded(self):
        """Port 80 is already allowed, not returned."""
        ports = otel_proxy_ports("http://collector:80")
        assert ports == []

    def test_custom_port(self):
        """Custom port is returned."""
        ports = otel_proxy_ports("http://collector:9090")
        assert ports == [9090]

    def test_default_port(self):
        """Default port 4318 is returned when no port specified."""
        ports = otel_proxy_ports("http://collector")
        assert ports == [4318]
