"""Tests for domain alias expansion."""

from __future__ import annotations

from paude.domains import (
    BASE_ALIASES,
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

    def test_expand_default_includes_vertexai_and_python(self):
        """'default' expands to vertexai + python domains."""
        result = expand_domains(["default"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all python domains
        for domain in DOMAIN_ALIASES["python"]:
            assert domain in result

    def test_expand_default_includes_github(self):
        """'default' expands to include github domains."""
        result = expand_domains(["default"])
        assert result is not None

        # Should include all github domains
        for domain in DOMAIN_ALIASES["github"]:
            assert domain in result

    def test_expand_default_includes_claude(self):
        """'default' expands to include claude domains."""
        result = expand_domains(["default"])
        assert result is not None

        # Should include all claude domains
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain in result

    def test_expand_vertexai_alias(self):
        """'vertexai' expands to vertexai domains."""
        result = expand_domains(["vertexai"])
        assert result is not None
        assert result == DOMAIN_ALIASES["vertexai"]

    def test_expand_python_alias(self):
        """'python' expands to python domains."""
        result = expand_domains(["python"])
        assert result is not None
        assert result == DOMAIN_ALIASES["python"]

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
        result = expand_domains(["vertexai", "vertexai", "oauth2.googleapis.com"])
        assert result is not None

        # Count occurrences of oauth2.googleapis.com (should be 1)
        count = result.count("oauth2.googleapis.com")
        assert count == 1

    def test_order_preserved(self):
        """Order is preserved (first occurrence wins)."""
        result = expand_domains(["python", "vertexai"])
        assert result is not None

        # python domains should come before vertexai domains
        python_first = result.index(DOMAIN_ALIASES["python"][0])
        vertexai_first = result.index(DOMAIN_ALIASES["vertexai"][0])
        assert python_first < vertexai_first

    def test_empty_list(self):
        """Empty list returns empty list."""
        result = expand_domains([])
        assert result == []

    def test_unknown_alias_treated_as_domain(self):
        """Unknown aliases are treated as raw domains."""
        result = expand_domains(["unknown-alias"])
        assert result == ["unknown-alias"]

    def test_regex_domain_passthrough(self):
        """Regex domains (~ prefix) pass through unchanged."""
        result = expand_domains(["~aiplatform\\.googleapis\\.com$"])
        assert result == ["~aiplatform\\.googleapis\\.com$"]

    def test_default_plus_custom_domain(self):
        """'default' + custom domain includes both."""
        result = expand_domains(["default", ".example.com"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all python domains
        for domain in DOMAIN_ALIASES["python"]:
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

        # Should NOT include python domains
        for domain in DOMAIN_ALIASES["python"]:
            assert domain not in result

    def test_wildcard_dedup_removes_subdomains(self):
        """Wildcard .example.com removes foo.example.com from results."""
        result = expand_domains(["foo.example.com", ".example.com"])
        assert result is not None
        assert ".example.com" in result
        assert "foo.example.com" not in result

    def test_wildcard_dedup_removes_exact_base(self):
        """Wildcard .example.com removes example.com from results."""
        result = expand_domains(["example.com", ".example.com"])
        assert result is not None
        assert ".example.com" in result
        assert "example.com" not in result

    def test_wildcard_dedup_with_mixed_aliases(self):
        """Dedup works across aliases and user-supplied domains."""
        result = expand_domains(["nodejs", "custom.npmjs.org"])
        assert result is not None
        assert ".npmjs.org" in result
        # custom.npmjs.org is a subdomain of .npmjs.org, should be removed
        assert "custom.npmjs.org" not in result

    def test_wildcard_dedup_preserves_unrelated(self):
        """Wildcard dedup does not remove unrelated domains."""
        result = expand_domains([".example.com", "other.org", "foo.example.com"])
        assert result is not None
        assert ".example.com" in result
        assert "other.org" in result
        assert "foo.example.com" not in result

    def test_wildcard_dedup_preserves_regex(self):
        """Wildcard dedup does not affect regex domains."""
        result = expand_domains(["~example\\.com$", ".example.com"])
        assert result is not None
        assert "~example\\.com$" in result
        assert ".example.com" in result


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

    def test_python_domains_show_alias(self):
        """Full python domains show alias name."""
        domains = list(DOMAIN_ALIASES["python"])
        result = format_domains_for_display(domains)
        assert "python" in result

    def test_mixed_aliases_both_shown(self):
        """Both aliases shown when both are present."""
        domains = list(DOMAIN_ALIASES["vertexai"]) + list(DOMAIN_ALIASES["python"])
        result = format_domains_for_display(domains)
        assert "vertexai" in result
        assert "python" in result

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

    def test_default_aliases_include_python(self):
        """DEFAULT_ALIASES includes 'python' (not 'pypi')."""
        assert "python" in DEFAULT_ALIASES
        assert "pypi" not in DEFAULT_ALIASES

    def test_vertexai_has_googleapis(self):
        """vertexai alias includes specific googleapis.com subdomains."""
        assert any("googleapis.com" in d for d in DOMAIN_ALIASES["vertexai"])

    def test_vertexai_no_broad_wildcards(self):
        """vertexai alias must NOT contain broad wildcards."""
        vertexai = DOMAIN_ALIASES["vertexai"]
        assert ".googleapis.com" not in vertexai, (
            "broad .googleapis.com wildcard not allowed"
        )
        assert ".google.com" not in vertexai, "broad .google.com wildcard not allowed"
        assert ".gstatic.com" not in vertexai, ".gstatic.com not needed for API auth"

    def test_vertexai_has_required_auth_domains(self):
        """vertexai alias includes required Google auth domains."""
        vertexai = DOMAIN_ALIASES["vertexai"]
        assert "accounts.google.com" in vertexai
        assert "oauth2.googleapis.com" in vertexai

    def test_vertexai_aiplatform_uses_regex(self):
        """vertexai alias uses regex for aiplatform to match regional endpoints."""
        vertexai = DOMAIN_ALIASES["vertexai"]
        # Must NOT use .aiplatform.googleapis.com (subdomain match fails for
        # REGION-aiplatform.googleapis.com since hyphens create sibling domains)
        assert ".aiplatform.googleapis.com" not in vertexai
        # Must use regex pattern that matches both aiplatform.googleapis.com
        # and regional endpoints like us-east5-aiplatform.googleapis.com
        regex_entries = [d for d in vertexai if d.startswith("~")]
        assert any("aiplatform" in d for d in regex_entries)

    def test_python_has_pypi_org(self):
        """python alias includes pypi.org."""
        assert any("pypi.org" in d for d in DOMAIN_ALIASES["python"])

    def test_python_has_pytorch(self):
        """python alias includes download.pytorch.org."""
        assert ".pytorch.org" in DOMAIN_ALIASES["python"]

    def test_claude_has_claude_ai(self):
        """claude alias includes .claude.ai."""
        assert any(".claude.ai" in d for d in DOMAIN_ALIASES["claude"])


class TestPypiBackwardCompatibility:
    """Tests for 'pypi' backward-compatible alias."""

    def test_pypi_alias_exists(self):
        """'pypi' alias exists in DOMAIN_ALIASES."""
        assert "pypi" in DOMAIN_ALIASES

    def test_pypi_expands_to_same_as_python(self):
        """'pypi' expands to the same domains as 'python'."""
        assert DOMAIN_ALIASES["pypi"] is DOMAIN_ALIASES["python"]

    def test_pypi_alias_expands_correctly(self):
        """expand_domains(['pypi']) returns same result as expand_domains(['python'])."""
        pypi_result = expand_domains(["pypi"])
        python_result = expand_domains(["python"])
        assert pypi_result == python_result

    def test_pypi_includes_pytorch(self):
        """'pypi' backward-compat alias also includes pytorch."""
        result = expand_domains(["pypi"])
        assert result is not None
        assert ".pytorch.org" in result


class TestGolangAlias:
    """Tests for the 'golang' domain alias."""

    def test_golang_alias_exists(self):
        """'golang' alias exists in DOMAIN_ALIASES."""
        assert "golang" in DOMAIN_ALIASES

    def test_golang_not_in_defaults(self):
        """'golang' is NOT in DEFAULT_ALIASES (opt-in only)."""
        assert "golang" not in DEFAULT_ALIASES

    def test_golang_expands_to_correct_domains(self):
        """'golang' expands to Go ecosystem domains."""
        result = expand_domains(["golang"])
        assert result is not None
        assert "go.dev" in result
        assert "dl.google.com" in result
        assert "proxy.golang.org" in result
        assert "sum.golang.org" in result
        assert "storage.googleapis.com" in result

    def test_golang_dedup_with_vertexai(self):
        """storage.googleapis.com is deduplicated when both vertexai and golang are used."""
        result = expand_domains(["vertexai", "golang"])
        assert result is not None
        count = result.count("storage.googleapis.com")
        assert count == 1

    def test_golang_in_format_display(self):
        """format_domains_for_display recognizes golang alias."""
        domains = expand_domains(["golang"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "golang" in result


class TestNodejsAlias:
    """Tests for the 'nodejs' domain alias."""

    def test_nodejs_alias_exists(self):
        """'nodejs' alias exists in DOMAIN_ALIASES."""
        assert "nodejs" in DOMAIN_ALIASES

    def test_nodejs_not_in_defaults(self):
        """'nodejs' is NOT in DEFAULT_ALIASES (opt-in only)."""
        assert "nodejs" not in DEFAULT_ALIASES

    def test_nodejs_expands_to_correct_domains(self):
        """'nodejs' expands to Node.js ecosystem domains."""
        result = expand_domains(["nodejs"])
        assert result is not None
        assert ".nodejs.org" in result
        assert ".npmjs.org" in result
        assert ".yarnpkg.com" in result
        # registry.npmjs.org is redundant with .npmjs.org
        assert "registry.npmjs.org" not in result

    def test_nodejs_in_format_display(self):
        """format_domains_for_display recognizes nodejs alias."""
        domains = expand_domains(["nodejs"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "nodejs" in result


class TestRustAlias:
    """Tests for the 'rust' domain alias."""

    def test_rust_alias_exists(self):
        """'rust' alias exists in DOMAIN_ALIASES."""
        assert "rust" in DOMAIN_ALIASES

    def test_rust_not_in_defaults(self):
        """'rust' is NOT in DEFAULT_ALIASES (opt-in only)."""
        assert "rust" not in DEFAULT_ALIASES

    def test_rust_expands_to_correct_domains(self):
        """'rust' expands to Rust ecosystem domains."""
        result = expand_domains(["rust"])
        assert result is not None
        assert "crates.io" in result
        assert "static.crates.io" in result
        assert "static.rust-lang.org" in result

    def test_rust_in_format_display(self):
        """format_domains_for_display recognizes rust alias."""
        domains = expand_domains(["rust"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "rust" in result


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


class TestClaudeAlias:
    """Tests for the 'claude' domain alias."""

    def test_claude_alias_expands_to_correct_domains(self):
        """'claude' expands to .claude.ai and .anthropic.com."""
        result = expand_domains(["claude"])
        assert result is not None
        assert ".claude.ai" in result
        assert ".anthropic.com" in result

    def test_claude_alias_combined_with_default(self):
        """'default' + 'claude' includes vertexai, python, github, and claude domains."""
        result = expand_domains(["default", "claude"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all python domains
        for domain in DOMAIN_ALIASES["python"]:
            assert domain in result

        # Should include all github domains
        for domain in DOMAIN_ALIASES["github"]:
            assert domain in result

        # Should include all claude domains
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain in result

    def test_claude_alias_in_format_display(self):
        """format_domains_for_display recognizes claude alias and shows alias name."""
        domains = expand_domains(["claude"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "claude" in result

    def test_claude_domains_no_duplicates(self):
        """expand_domains(['claude', 'claude']) has no duplicates."""
        result = expand_domains(["claude", "claude"])
        assert result is not None
        assert len(result) == len(set(result))


class TestGithubAlias:
    """Tests for the 'github' domain alias."""

    def test_github_alias_expands_to_correct_domains(self):
        """'github' expands to github.com, api.github.com, raw.githubusercontent.com."""
        result = expand_domains(["github"])
        assert result is not None
        assert "github.com" in result
        assert "api.github.com" in result
        assert "raw.githubusercontent.com" in result
        assert "release-assets.githubusercontent.com" in result
        assert "results-receiver.actions.githubusercontent.com" in result

    def test_github_alias_combined_with_default(self):
        """'default' + 'github' includes vertexai, python, and github domains."""
        result = expand_domains(["default", "github"])
        assert result is not None

        # Should include all vertexai domains
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result

        # Should include all python domains
        for domain in DOMAIN_ALIASES["python"]:
            assert domain in result

        # Should include all github domains
        for domain in DOMAIN_ALIASES["github"]:
            assert domain in result

    def test_github_alias_in_format_display(self):
        """format_domains_for_display recognizes github alias and shows alias name."""
        domains = expand_domains(["github"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "github" in result

    def test_github_domains_no_duplicates(self):
        """expand_domains(['github', 'github']) has no duplicates."""
        result = expand_domains(["github", "github"])
        assert result is not None
        assert len(result) == len(set(result))


class TestGeminiAlias:
    """Tests for the 'gemini' domain alias."""

    def test_gemini_alias_exists(self):
        """'gemini' alias exists in DOMAIN_ALIASES."""
        assert "gemini" in DOMAIN_ALIASES

    def test_gemini_not_in_defaults(self):
        """'gemini' is NOT in DEFAULT_ALIASES (opt-in only)."""
        assert "gemini" not in DEFAULT_ALIASES

    def test_gemini_expands_to_correct_domains(self):
        """'gemini' expands to Gemini-specific API domains."""
        result = expand_domains(["gemini"])
        assert result is not None
        assert "cloudcode-pa.googleapis.com" in result
        assert "play.googleapis.com" in result

    def test_gemini_in_format_display(self):
        """format_domains_for_display recognizes gemini alias."""
        domains = expand_domains(["gemini"])
        assert domains is not None
        result = format_domains_for_display(domains)
        assert "gemini" in result


class TestBaseAliases:
    """Tests for BASE_ALIASES constant."""

    def test_base_aliases_defined(self):
        """BASE_ALIASES references valid aliases."""
        for alias in BASE_ALIASES:
            assert alias in DOMAIN_ALIASES

    def test_base_aliases_does_not_include_claude(self):
        """BASE_ALIASES does not include agent-specific aliases."""
        assert "claude" not in BASE_ALIASES
        assert "gemini" not in BASE_ALIASES

    def test_base_aliases_includes_shared(self):
        """BASE_ALIASES includes shared aliases."""
        assert "vertexai" in BASE_ALIASES
        assert "python" in BASE_ALIASES
        assert "github" in BASE_ALIASES


class TestExpandDomainsWithExtraAliases:
    """Tests for expand_domains with extra_aliases parameter."""

    def test_extra_aliases_none_uses_default(self):
        """extra_aliases=None falls back to DEFAULT_ALIASES."""
        result = expand_domains(["default"], extra_aliases=None)
        assert result is not None
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain in result

    def test_extra_aliases_empty_uses_base_only(self):
        """extra_aliases=[] uses only BASE_ALIASES."""
        result = expand_domains(["default"], extra_aliases=[])
        assert result is not None
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain not in result

    def test_extra_aliases_claude(self):
        """extra_aliases=['claude'] includes same domains as DEFAULT_ALIASES."""
        default_result = expand_domains(["default"])
        extra_result = expand_domains(["default"], extra_aliases=["claude"])
        assert default_result is not None
        assert extra_result is not None
        assert set(default_result) == set(extra_result)

    def test_extra_aliases_gemini_nodejs(self):
        """extra_aliases=['gemini', 'nodejs'] adds gemini and nodejs to base."""
        result = expand_domains(["default"], extra_aliases=["gemini", "nodejs"])
        assert result is not None
        # Base aliases present
        for domain in DOMAIN_ALIASES["vertexai"]:
            assert domain in result
        for domain in DOMAIN_ALIASES["python"]:
            assert domain in result
        for domain in DOMAIN_ALIASES["github"]:
            assert domain in result
        # Gemini extras present
        for domain in DOMAIN_ALIASES["gemini"]:
            assert domain in result
        for domain in DOMAIN_ALIASES["nodejs"]:
            assert domain in result
        # Claude NOT present
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain not in result

    def test_extra_aliases_does_not_affect_explicit_aliases(self):
        """extra_aliases only affects 'default', not explicit alias usage."""
        result = expand_domains(["claude"], extra_aliases=["gemini"])
        assert result is not None
        for domain in DOMAIN_ALIASES["claude"]:
            assert domain in result


class TestMessagingDomainAliases:
    """Tests for messaging platform domain aliases."""

    def test_openclaw_alias_exists(self):
        assert "openclaw" in DOMAIN_ALIASES

    def test_whatsapp_alias_exists(self):
        assert "whatsapp" in DOMAIN_ALIASES
        result = expand_domains(["whatsapp"])
        assert result is not None
        assert any("whatsapp" in d for d in result)

    def test_telegram_alias_exists(self):
        assert "telegram" in DOMAIN_ALIASES
        result = expand_domains(["telegram"])
        assert result is not None
        assert "api.telegram.org" in result

    def test_discord_alias_exists(self):
        assert "discord" in DOMAIN_ALIASES
        result = expand_domains(["discord"])
        assert result is not None
        assert any("discord" in d for d in result)

    def test_slack_alias_exists(self):
        assert "slack" in DOMAIN_ALIASES
        result = expand_domains(["slack"])
        assert result is not None
        assert any("slack" in d for d in result)

    def test_openclaw_with_messaging_aliases(self):
        """Can combine openclaw with messaging aliases."""
        result = expand_domains(
            ["default", "telegram", "discord"], extra_aliases=["openclaw"]
        )
        assert result is not None
        assert "api.telegram.org" in result
        assert any("discord" in d for d in result)
