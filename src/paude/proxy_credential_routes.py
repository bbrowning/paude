"""Custom credential routing for the paude-proxy sidecar.

paude-proxy injects each provider credential only for that provider's own
domains (e.g. ``ANTHROPIC_API_KEY`` only for ``*.anthropic.com``). A
``--credential-domain PROVIDER=HOST[:PORT]`` spec routes a provider's secret
to an additional host, such as a company AI gateway, by handing the proxy a
custom routing file through ``PAUDE_PROXY_CREDENTIALS_CONFIG``.

paude-proxy *replaces* its built-in routing table with a custom file rather
than merging, so the file paude writes is the built-in table plus the custom
routes, which go first because the proxy uses the first matching route.
"""

from __future__ import annotations

import copy
from typing import Any

from paude.endpoints import authority_host, normalize_allowed_endpoints

CREDENTIAL_ROUTES_PATH = "/data/auth/credential-routes.json"
CREDENTIAL_ROUTES_ENV = "PAUDE_PROXY_CREDENTIALS_CONFIG"

_DEFAULT_PORT = 443

# Copy of paude-proxy's embedded internal/credentials/credentials.json at the
# PAUDE_PROXY_VERSION pinned in containers/proxy/Dockerfile. A custom routing
# file replaces this table entirely, so it MUST be kept in sync whenever that
# pin is bumped, or sessions using --credential-domain lose built-in routes.
DEFAULT_CREDENTIAL_ROUTES: list[dict[str, Any]] = [
    {
        "env_var": "CHATGPT_AUTH_FILE",
        "injector": "chatgpt",
        "params": {"path_prefix": "/backend-api"},
        "domains": ["chatgpt.com"],
    },
    {
        "env_var": "ANTHROPIC_OAUTH_CREDS_FILE",
        "injector": "anthropic_oauth",
        "domains": ["api.anthropic.com", ".claude.ai"],
    },
    {
        "env_var": "CLAUDE_CODE_OAUTH_TOKEN",
        "injector": "bearer",
        "domains": ["api.anthropic.com", ".claude.ai"],
    },
    {
        "env_var": "ANTHROPIC_API_KEY",
        "injector": "api_key",
        "params": {"header_name": "x-api-key"},
        "domains": [".anthropic.com"],
    },
    {
        "env_var": "OPENAI_API_KEY",
        "injector": "bearer",
        "domains": [".openai.com", "chatgpt.com", ".chatgpt.com"],
    },
    {
        "env_var": "CURSOR_API_KEY",
        "injector": "bearer",
        "domains": [".cursor.com", ".cursorapi.com"],
    },
    {
        "env_var": "GH_TOKEN",
        "injector": "bearer",
        "domains": ["api.github.com"],
    },
    {
        "env_var": "GOOGLE_APPLICATION_CREDENTIALS",
        "injector": "gcloud",
        "domains": [".googleapis.com"],
    },
]


def _routable_secret_vars(provider_name: str) -> list[str]:
    """Return the provider's secret env vars that have a default route."""
    from paude.providers import get_provider

    routable = {route["env_var"] for route in DEFAULT_CREDENTIAL_ROUTES}
    return [
        var for var in get_provider(provider_name).secret_env_vars if var in routable
    ]


def split_credential_domain(spec: str) -> tuple[str, str]:
    """Split a normalized ``provider=host:port`` spec."""
    provider, _, authority = spec.partition("=")
    return provider, authority


def parse_credential_domains(
    values: list[str] | None,
    providers: list[str],
) -> list[str]:
    """Validate ``PROVIDER=HOST[:PORT]`` specs into ``provider=host:port``.

    Raises:
        ValueError: On a malformed spec, a provider the session does not
            configure, or a provider with no API-key credential to route.
    """
    normalized: list[str] = []
    for value in values or []:
        provider, sep, target = (part.strip() for part in value.partition("="))
        if not sep or not provider or not target:
            raise ValueError(
                f"Invalid credential domain '{value}'; expected PROVIDER=HOST[:PORT]."
            )
        if provider not in providers:
            configured = ", ".join(providers) or "none"
            raise ValueError(
                f"Credential domain '{value}' names provider '{provider}', which "
                f"this session does not configure (providers: {configured})."
            )
        if not _routable_secret_vars(provider):
            raise ValueError(
                f"Provider '{provider}' has no API-key credential that can be "
                "routed to a custom domain."
            )
        authority = target
        if authority.endswith("]") or ":" not in authority:
            authority = f"{authority}:{_DEFAULT_PORT}"
        try:
            (canonical,) = normalize_allowed_endpoints([authority])
        except ValueError as exc:
            raise ValueError(f"Invalid credential domain '{value}': {exc}") from None
        spec = f"{provider}={canonical}"
        if spec not in normalized:
            normalized.append(spec)
    return normalized


def credential_domain_hosts(specs: list[str]) -> list[str]:
    """Return the hosts the specs route credentials to, for the allowlist."""
    return list(
        dict.fromkeys(authority_host(split_credential_domain(s)[1]) for s in specs)
    )


def credential_domain_endpoints(specs: list[str]) -> list[str]:
    """Return the nonstandard-port authorities the specs need allowed."""
    authorities = (split_credential_domain(spec)[1] for spec in specs)
    return list(
        dict.fromkeys(a for a in authorities if not a.endswith(f":{_DEFAULT_PORT}"))
    )


def build_credential_routes_config(specs: list[str]) -> dict[str, Any]:
    """Build the full paude-proxy routing file for the given specs."""
    defaults = {route["env_var"]: route for route in DEFAULT_CREDENTIAL_ROUTES}
    custom: list[dict[str, Any]] = []
    for spec in specs:
        provider, authority = split_credential_domain(spec)
        host = authority_host(authority)
        for var in _routable_secret_vars(provider):
            route = copy.deepcopy(defaults[var])
            route["domains"] = [host]
            custom.append(route)
    return {"credentials": custom + copy.deepcopy(DEFAULT_CREDENTIAL_ROUTES)}
