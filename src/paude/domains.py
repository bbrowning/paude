"""Domain alias definitions and expansion logic for --allowed-domains."""

from __future__ import annotations

import ipaddress
import re

# Domain aliases for common use cases
DOMAIN_ALIASES: dict[str, list[str]] = {
    "vertexai": [
        # Google OAuth / authentication
        "accounts.google.com",
        "oauth2.googleapis.com",
        "www.googleapis.com",
        # Vertex AI API (regional endpoints: REGION-aiplatform.googleapis.com)
        # Uses regex (~) because regional endpoints use hyphens, not subdomains
        "~aiplatform\\.googleapis\\.com$",
        # Google Cloud resource and project management
        "cloudresourcemanager.googleapis.com",
        # Service account impersonation and workload identity
        "iamcredentials.googleapis.com",
        "sts.googleapis.com",
        # Cloud Storage (model artifacts)
        "storage.googleapis.com",
    ],
    "claude": [
        ".claude.ai",
        ".anthropic.com",
    ],
    "chatgpt": [
        "chatgpt.com",
        ".chatgpt.com",
        "auth.openai.com",
    ],
    "gemini": [
        "cloudcode-pa.googleapis.com",
        "play.googleapis.com",
    ],
    "cursor": [
        ".cursor.com",
        ".cursor.sh",
        ".cursor-cdn.com",
        ".cursorapi.com",
    ],
    "python": [
        ".pypi.org",
        ".pythonhosted.org",
        ".pytorch.org",
    ],
    "golang": [
        "go.dev",
        "dl.google.com",
        "proxy.golang.org",
        "sum.golang.org",
        "storage.googleapis.com",
    ],
    "nodejs": [
        ".nodejs.org",
        ".npmjs.org",
        ".yarnpkg.com",
    ],
    "rust": [
        "crates.io",
        "static.crates.io",
        "static.rust-lang.org",
    ],
    "github": [
        "github.com",
        "api.github.com",
        "raw.githubusercontent.com",
        "codeload.github.com",
        "release-assets.githubusercontent.com",
        "results-receiver.actions.githubusercontent.com",
    ],
    "openai": [
        ".openai.com",
    ],
    "opencode": [
        "opencode.ai",
        ".opencode.ai",
    ],
    "openclaw": [
        ".anthropic.com",
        ".openai.com",
        ".duckduckgo.com",  # built-in web_search provider, no API key needed
        "wttr.in",  # built-in weather skill, no API key needed
        "api.open-meteo.com",  # built-in weather skill fallback
    ],
    "clawhub": [
        "clawhub.ai",
        ".clawhub.ai",
        "registry.npmjs.org",  # skill packages distributed via npm
    ],
    "whatsapp": [
        "web.whatsapp.com",
        ".whatsapp.net",
    ],
    "telegram": [
        "api.telegram.org",
    ],
    "discord": [
        ".discord.com",
        "gateway.discord.gg",
        ".discordapp.com",
    ],
    "slack": [
        ".slack.com",
    ],
}

# Backward-compatible aliases
DOMAIN_ALIASES["pypi"] = DOMAIN_ALIASES["python"]
DOMAIN_ALIASES["codex"] = DOMAIN_ALIASES["chatgpt"]

# Base aliases shared across all agents
BASE_ALIASES = ["vertexai", "python", "github"]

# Default aliases when --allowed-domains is not specified (backward compat)
DEFAULT_ALIASES = BASE_ALIASES + ["claude"]


def expand_domains(
    domains: list[str],
    extra_aliases: list[str] | None = None,
) -> list[str]:
    """Expand domain aliases to a list of actual domains.

    Args:
        domains: List of domains or aliases. Special values:
            - "all": Returns [] (unrestricted network, proxy allows all)
            - "default": Expands to BASE_ALIASES + extra_aliases
              (falls back to DEFAULT_ALIASES if extra_aliases is None)
            - Alias names (e.g., "claude", "vertexai"): Expand to domain lists
            - Raw domains (e.g., ".example.com"): Pass through unchanged
        extra_aliases: Agent-specific aliases to add on top of BASE_ALIASES
            when expanding "default". If None, falls back to DEFAULT_ALIASES
            for backward compatibility.

    Returns:
        List of expanded domains. Empty list means unrestricted (proxy
        allows all domains). Duplicates are removed while preserving order.
    """
    # Check for "all" - means unrestricted network (proxy allows everything)
    if "all" in domains:
        return []

    expanded: list[str] = []
    seen: set[str] = set()

    # Determine which aliases to use for "default"
    if extra_aliases is not None:
        default_aliases = BASE_ALIASES + extra_aliases
    else:
        default_aliases = DEFAULT_ALIASES

    for domain in domains:
        # Handle "default" alias
        if domain == "default":
            for alias in default_aliases:
                for d in DOMAIN_ALIASES.get(alias, []):
                    if d not in seen:
                        expanded.append(d)
                        seen.add(d)
        # Handle known aliases
        elif domain in DOMAIN_ALIASES:
            for d in DOMAIN_ALIASES[domain]:
                if d not in seen:
                    expanded.append(d)
                    seen.add(d)
        # Pass through raw domains
        else:
            if domain not in seen:
                expanded.append(domain)
                seen.add(domain)

    return expanded


def host_matches_allowed_domains(host: str, domains: list[str]) -> bool:
    """Return whether ``host`` is covered by an allowed-domain entry.

    The comparison mirrors the proxy's exact, suffix, and regex forms. Domain
    aliases are expanded for callers that have not already resolved them, and
    an empty expanded list means unrestricted access.
    """
    expanded = expand_domains(domains)
    if not expanded:
        return True

    canonical_host = _canonical_match_host(host)
    return any(_domain_matches_host(canonical_host, domain) for domain in expanded)


def _domain_matches_host(host: str, domain: str) -> bool:
    """Match one proxy allowed-domain entry without enforcing policy."""
    if domain.startswith("~"):
        try:
            return re.search(domain[1:], host) is not None
        except re.error:
            # This check is advisory. Avoid a false warning when the proxy's
            # regex dialect accepts syntax that Python's does not.
            return True
    if domain.startswith("."):
        suffix = _canonical_match_host(domain[1:])
        return host == suffix or host.endswith(f".{suffix}")
    return host == _canonical_match_host(domain)


def _canonical_match_host(host: str) -> str:
    """Canonicalize a host for advisory policy comparisons."""
    host = host.lower().removesuffix(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return address.compressed


def is_unrestricted(domains: list[str]) -> bool:
    """Check if the domain configuration allows unrestricted network access.

    Args:
        domains: Expanded domains list (output of expand_domains).

    Returns:
        True if network is unrestricted (empty list).
    """
    return len(domains) == 0


def format_domains_for_display(domains: list[str]) -> str:
    """Format expanded domains for display.

    Args:
        domains: List of expanded domains. Empty list means unrestricted.

    Returns:
        Human-readable string describing the network access.
    """
    if not domains:
        return "unrestricted (all domains allowed)"

    # Group by alias if possible
    aliases_used = []
    remaining_domains = set(domains)

    for alias, alias_domains in DOMAIN_ALIASES.items():
        alias_set = set(alias_domains)
        if alias_set.issubset(remaining_domains):
            aliases_used.append(alias)
            remaining_domains -= alias_set

    parts = []
    if aliases_used:
        parts.append(", ".join(aliases_used))
    if remaining_domains:
        # Show a few custom domains, truncate if many
        custom = sorted(remaining_domains)
        if len(custom) <= 3:
            parts.append(", ".join(custom))
        else:
            parts.append(f"{custom[0]}, {custom[1]}, ... (+{len(custom) - 2} more)")

    return " + ".join(parts) if parts else "none"
