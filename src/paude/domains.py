"""Domain alias definitions and expansion logic for --allowed-domains."""

from __future__ import annotations

# Domain aliases for common use cases
DOMAIN_ALIASES: dict[str, list[str]] = {
    "vertexai": [
        ".googleapis.com",
        ".google.com",
        "accounts.google.com",
        "oauth2.googleapis.com",
        ".gstatic.com",
        ".cloudresourcemanager.googleapis.com",
    ],
    "pypi": [
        ".pypi.org",
        ".pythonhosted.org",
    ],
}

# Default aliases when --allowed-domains is not specified
DEFAULT_ALIASES = ["vertexai", "pypi"]


def expand_domains(domains: list[str]) -> list[str] | None:
    """Expand domain aliases to a list of actual domains.

    Args:
        domains: List of domains or aliases. Special values:
            - "all": Returns None (unrestricted network)
            - "default": Expands to vertexai + pypi
            - "vertexai", "pypi": Expand to their respective domain lists
            - Raw domains (e.g., ".example.com"): Pass through unchanged

    Returns:
        List of expanded domains, or None if "all" is specified (unrestricted).
        Duplicates are removed while preserving order.
    """
    # Check for "all" - means unrestricted network
    if "all" in domains:
        return None

    expanded: list[str] = []
    seen: set[str] = set()

    for domain in domains:
        # Handle "default" alias
        if domain == "default":
            for alias in DEFAULT_ALIASES:
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
