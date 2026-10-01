"""Tests for custom proxy credential routing (--credential-domain)."""

from __future__ import annotations

import pytest

from paude.proxy_credential_routes import (
    DEFAULT_CREDENTIAL_ROUTES,
    build_credential_routes_config,
    credential_domain_endpoints,
    credential_domain_hosts,
    parse_credential_domains,
)


class TestParseCredentialDomains:
    def test_defaults_port_and_normalizes_host(self) -> None:
        assert parse_credential_domains(
            ["anthropic=GW.Example.COM."], ["anthropic"]
        ) == ["anthropic=gw.example.com:443"]

    def test_keeps_explicit_port(self) -> None:
        assert parse_credential_domains(
            ["anthropic=gw.example.com:8443"], ["anthropic"]
        ) == ["anthropic=gw.example.com:8443"]

    def test_deduplicates(self) -> None:
        specs = ["anthropic=gw.example.com", "anthropic=gw.example.com:443"]
        assert parse_credential_domains(specs, ["anthropic"]) == [
            "anthropic=gw.example.com:443"
        ]

    def test_none_is_empty(self) -> None:
        assert parse_credential_domains(None, ["anthropic"]) == []

    @pytest.mark.parametrize("spec", ["anthropic", "=gw.example.com", "anthropic="])
    def test_rejects_malformed_spec(self, spec: str) -> None:
        with pytest.raises(ValueError, match="expected PROVIDER=HOST"):
            parse_credential_domains([spec], ["anthropic"])

    def test_rejects_provider_not_in_session(self) -> None:
        with pytest.raises(ValueError, match="does not configure"):
            parse_credential_domains(["openai=gw.example.com"], ["anthropic"])

    def test_rejects_provider_without_api_key(self) -> None:
        with pytest.raises(ValueError, match="no API-key credential"):
            parse_credential_domains(["vertex=gw.example.com"], ["vertex"])

    @pytest.mark.parametrize(
        "target", ["https://gw.example.com", "*.example.com", ".example.com"]
    )
    def test_rejects_scheme_and_wildcards(self, target: str) -> None:
        with pytest.raises(ValueError, match="Invalid credential domain"):
            parse_credential_domains([f"anthropic={target}"], ["anthropic"])


class TestHostsAndEndpoints:
    def test_hosts_strip_ports(self) -> None:
        specs = ["anthropic=gw.example.com:8443", "openai=gw.example.com:443"]
        assert credential_domain_hosts(specs) == ["gw.example.com"]

    def test_only_nonstandard_ports_become_endpoints(self) -> None:
        specs = ["anthropic=a.example.com:443", "openai=b.example.com:8443"]
        assert credential_domain_endpoints(specs) == ["b.example.com:8443"]


class TestBuildConfig:
    def test_custom_route_first_then_all_defaults(self) -> None:
        config = build_credential_routes_config(["anthropic=gw.example.com:8443"])

        routes = config["credentials"]
        assert routes[0] == {
            "env_var": "ANTHROPIC_API_KEY",
            "injector": "api_key",
            "params": {"header_name": "x-api-key"},
            "domains": ["gw.example.com"],
        }
        assert routes[1:] == DEFAULT_CREDENTIAL_ROUTES

    def test_does_not_mutate_defaults(self) -> None:
        before = [dict(route) for route in DEFAULT_CREDENTIAL_ROUTES]
        build_credential_routes_config(["anthropic=gw.example.com:443"])
        assert before == DEFAULT_CREDENTIAL_ROUTES

    def test_no_specs_is_default_table(self) -> None:
        assert build_credential_routes_config([]) == {
            "credentials": DEFAULT_CREDENTIAL_ROUTES
        }
