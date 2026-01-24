"""Tests for domain alias expansion."""

from __future__ import annotations

from paude.domains import (
    DEFAULT_ALIASES,
    DOMAIN_ALIASES,
    expand_domains,
    format_domains_for_display,
    is_unrestricted,
)


class TestExpandDomains:
    """Tests for expand_domains function."""

    def test_expand_all_returns_none(self):
        """'all' returns None (unrestricted network)."""
        result = expand_domains(["all"])
        assert result is None

    def test_expand_all_with_other_domains_returns_none(self):
        """'all' with other domains still returns None."""
        result = expand_domains(["vertexai", "all", ".example.com"])
        assert result is None

    def test_expand_default_includes_vertexai_and_pypi(self):
        """'default' expands to vertexai + pypi domains."""
        result = expand_domains(["default"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all pypi domains
        for domain in DOMAIN_ALIASES["pypi"]:
            assert domain in result

    def test_expand_vertexai_alias(self):
        """'vertexai' expands to vertexai domains."""
        result = expand_domains(["vertexai"])
        assert result is not None
        assert result == DOMAIN_ALIASES["vertexai"]

    def test_expand_pypi_alias(self):
        """'pypi' expands to pypi domains."""
        result = expand_domains(["pypi"])
        assert result is not None
        assert result == DOMAIN_ALIASES["pypi"]

    def test_raw_domain_passthrough(self):
        """Raw domains pass through unchanged."""
        result = expand_domains([".example.com", "api.github.com"])
        assert result == [".example.com", "api.github.com"]

    def test_mixed_aliases_and_raw_domains(self):
        """Mixed aliases and raw domains work together."""
        result = expand_domains(["vertexai", ".example.com"])
        assert result is not None

        # Should include vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include raw domain
        assert ".example.com" in result

    def test_deduplication(self):
        """Duplicate domains are removed."""
        result = expand_domains(["vertexai", "vertexai", ".googleapis.com"])
        assert result is not None

        # Count occurrences of .googleapis.com (should be 1)
        count = result.count(".googleapis.com")
        assert count == 1

    def test_order_preserved(self):
        """Order is preserved (first occurrence wins)."""
        result = expand_domains(["pypi", "vertexai"])
        assert result is not None

        # pypi domains should come before vertexai domains
        pypi_first = result.index(DOMAIN_ALIASES["pypi"][0])
        vertexai_first = result.index(DOMAIN_ALIASES["vertexai"][0])
        assert pypi_first < vertexai_first

    def test_empty_list(self):
        """Empty list returns empty list."""
        result = expand_domains([])
        assert result == []

    def test_unknown_alias_treated_as_domain(self):
        """Unknown aliases are treated as raw domains."""
        result = expand_domains(["unknown-alias"])
        assert result == ["unknown-alias"]

    def test_default_plus_custom_domain(self):
        """'default' + custom domain includes both."""
        result = expand_domains(["default", ".example.com"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all pypi domains
        for domain in DOMAIN_ALIASES["pypi"]:
            assert domain in result

        # Should include custom domain
        assert ".example.com" in result

    def test_custom_domain_alone_does_not_include_defaults(self):
        """Custom domain alone does NOT include defaults."""
        result = expand_domains([".example.com"])
        assert result == [".example.com"]

        # Should NOT include vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain not in result

        # Should NOT include pypi domains
        for domain in DOMAIN_ALIASES["pypi"]:
            assert domain not in result


class TestFormatDomainsForDisplay:
    """Tests for format_domains_for_display function."""

    def test_none_shows_unrestricted(self):
        """None shows unrestricted message."""
        result = format_domains_for_display(None)
        assert "unrestricted" in result

    def test_empty_list_shows_none(self):
        """Empty list shows none."""
        result = format_domains_for_display([])
        assert "none" in result

    def test_vertexai_domains_show_alias(self):
        """Full vertexai domains show alias name."""
        domains = list(DOMAIN_ALIASES["vertexai"])
        result = format_domains_for_display(domains)
        assert "vertexai" in result

    def test_pypi_domains_show_alias(self):
        """Full pypi domains show alias name."""
        domains = list(DOMAIN_ALIASES["pypi"])
        result = format_domains_for_display(domains)
        assert "pypi" in result

    def test_mixed_aliases_both_shown(self):
        """Both aliases shown when both are present."""
        domains = list(DOMAIN_ALIASES["vertexai"]) + list(DOMAIN_ALIASES["pypi"])
        result = format_domains_for_display(domains)
        assert "vertexai" in result
        assert "pypi" in result

    def test_custom_domains_shown(self):
        """Custom domains are displayed."""
        result = format_domains_for_display([".example.com"])
        assert ".example.com" in result

    def test_many_custom_domains_truncated(self):
        """Many custom domains are truncated."""
        domains = [f".domain{i}.com" for i in range(10)]
        result = format_domains_for_display(domains)
        # Should mention "more"
        assert "more" in result


class TestDomainAliases:
    """Tests for domain alias definitions."""

    def test_default_aliases_defined(self):
        """DEFAULT_ALIASES references valid aliases."""
        for alias in DEFAULT_ALIASES:
            assert alias in DOMAIN_ALIASES

    def test_vertexai_has_googleapis(self):
        """vertexai alias includes googleapis.com."""
        assert any(".googleapis.com" in d for d in DOMAIN_ALIASES["vertexai"])

    def test_pypi_has_pypi_org(self):
        """pypi alias includes pypi.org."""
        assert any("pypi.org" in d for d in DOMAIN_ALIASES["pypi"])


class TestIsUnrestricted:
    """Tests for is_unrestricted helper function."""

    def test_none_is_unrestricted(self):
        """None domains means unrestricted."""
        assert is_unrestricted(None) is True

    def test_empty_list_is_restricted(self):
        """Empty list is NOT unrestricted (no network access)."""
        assert is_unrestricted([]) is False

    def test_domain_list_is_restricted(self):
        """A list of domains is NOT unrestricted."""
        assert is_unrestricted([".googleapis.com", ".pypi.org"]) is False
