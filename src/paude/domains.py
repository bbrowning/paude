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


def format_domains_as_squid_acls(domains: list[str]) -> str:
    """Format deduplicated domains as squid ACL lines.

    Separates regex domains (~prefix) from normal domains to avoid
    mixing dstdomain and dstdom_regex under the same ACL name
    (squid 5.x crashes when they share a name).

    Args:
        domains: Deduplicated domain list (output of expand_domains).

    Returns:
        Newline-separated ACL block ready for squid.conf injection.
    """
    normal_lines: list[str] = []
    regex_lines: list[str] = []

    for domain in domains:
        if domain.startswith("~"):
            regex_lines.append(f"acl allowed_domains_regex dstdom_regex {domain[1:]}")
        else:
            normal_lines.append(f"acl allowed_domains dstdomain {domain}")

    # squid errors on undefined ACLs referenced in http_access rules;
    # both allowed_domains and allowed_domains_regex must always be defined
    if not normal_lines:
        normal_lines.append("acl allowed_domains dstdomain .invalid")
    if not regex_lines:
        regex_lines.append("acl allowed_domains_regex dstdom_regex ^$")

    return "\\n".join(normal_lines + regex_lines)


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
