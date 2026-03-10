"""Domain alias definitions and expansion logic for --allowed-domains."""

from __future__ import annotations

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
    "gemini": [
        "cloudcode-pa.googleapis.com",
        "play.googleapis.com",
    ],
    "python": [
        ".pypi.org",
        ".pythonhosted.org",
        "download.pytorch.org",
    ],
    "golang": [
        "go.dev",
        "dl.google.com",
        "proxy.golang.org",
        "sum.golang.org",
        "storage.googleapis.com",
    ],
    "nodejs": [
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
}

# Backward-compatible alias: pypi -> python
DOMAIN_ALIASES["pypi"] = DOMAIN_ALIASES["python"]

# Base aliases shared across all agents
BASE_ALIASES = ["vertexai", "python", "github"]

# Default aliases when --allowed-domains is not specified (backward compat)
DEFAULT_ALIASES = BASE_ALIASES + ["claude"]


def expand_domains(
    domains: list[str],
    extra_aliases: list[str] | None = None,
) -> list[str] | None:
    """Expand domain aliases to a list of actual domains.

    Args:
        domains: List of domains or aliases. Special values:
            - "all": Returns None (unrestricted network)
            - "default": Expands to BASE_ALIASES + extra_aliases
              (falls back to DEFAULT_ALIASES if extra_aliases is None)
            - Alias names (e.g., "claude", "vertexai"): Expand to domain lists
            - Raw domains (e.g., ".example.com"): Pass through unchanged
        extra_aliases: Agent-specific aliases to add on top of BASE_ALIASES
            when expanding "default". If None, falls back to DEFAULT_ALIASES
            for backward compatibility.

    Returns:
        List of expanded domains, or None if "all" is specified (unrestricted).
        Duplicates are removed while preserving order.
    """
    # Check for "all" - means unrestricted network
    if "all" in domains:
        return None

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

    return remove_wildcard_covered(expanded)


def remove_wildcard_covered(domains: list[str]) -> list[str]:
    """Remove domains that are already covered by a wildcard in the list.

    Squid treats .example.com as matching both example.com and *.example.com,
    so having both .example.com and foo.example.com is a fatal config error.

    Args:
        domains: List of domains (may include wildcards and regex entries).

    Returns:
        Filtered list with redundant domains removed, preserving order.
    """
    wildcards = [d for d in domains if d.startswith(".")]
    if not wildcards:
        return domains
    return [
        d
        for d in domains
        if d.startswith(".")
        or d.startswith("~")
        or not any(d == w[1:] or d.endswith(w) for w in wildcards)
    ]


def is_unrestricted(domains: list[str] | None) -> bool:
    """Check if the domain configuration allows unrestricted network access.

    Args:
        domains: Expanded domains list (output of expand_domains).

    Returns:
        True if network is unrestricted (domains is None).
    """
    return domains is None


def format_domains_for_display(domains: list[str] | None) -> str:
    """Format expanded domains for display.

    Args:
        domains: List of expanded domains or None (unrestricted).

    Returns:
        Human-readable string describing the network access.
    """
    if domains is None:
        return "unrestricted (all domains allowed)"

    if not domains:
        return "none (no network access)"

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
