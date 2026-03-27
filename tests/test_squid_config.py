"""Tests for squid proxy configuration consistency."""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

import pytest

from paude.domains import format_domains_as_squid_acls

PROXY_DIR = Path(__file__).parent.parent / "containers" / "proxy"
SQUID_CONF = PROXY_DIR / "squid.conf"
ENTRYPOINT_SH = PROXY_DIR / "entrypoint.sh"


class TestSquidConfACLTypes:
    """Verify squid.conf doesn't mix ACL types under the same name."""

    def _parse_acl_types(self, text: str) -> dict[str, set[str]]:
        """Parse ACL name -> set of type keywords from config text."""
        acl_types: dict[str, set[str]] = {}
        for line in text.splitlines():
            m = re.match(r"^acl\s+(\S+)\s+(dstdomain|dstdom_regex|dst)\s", line)
            if m:
                name, typ = m.group(1), m.group(2)
                acl_types.setdefault(name, set()).add(typ)
        return acl_types

    def test_no_mixed_acl_types(self):
        """Each ACL name must use only one type keyword (dstdomain OR dstdom_regex)."""
        content = SQUID_CONF.read_text()
        acl_types = self._parse_acl_types(content)
        for name, types in acl_types.items():
            assert len(types) == 1, (
                f"ACL '{name}' mixes types {types}; "
                "squid 5.x crashes when dstdomain and dstdom_regex share a name"
            )

    def test_allowed_domains_regex_referenced_in_access_log(self):
        """access_log line must exclude both allowed_domains and allowed_domains_regex."""
        content = SQUID_CONF.read_text()
        log_lines = [
            line for line in content.splitlines() if line.startswith("access_log")
        ]
        assert len(log_lines) == 1
        assert "!allowed_domains_regex" in log_lines[0]
        assert "!allowed_domains" in log_lines[0]

    def test_allowed_domains_regex_has_http_access(self):
        """http_access allow must reference allowed_domains_regex."""
        content = SQUID_CONF.read_text()
        assert "http_access allow allowed_domains_regex" in content


class TestFormatDomainsAsSquidACLs:
    """Test Python-side ACL formatting (replaces shell-side domain parsing).

    The output uses literal \\n separators (not real newlines) so it can be
    passed through sed's s/// command for squid.conf injection.
    """

    @staticmethod
    def _expand(acls: str) -> str:
        """Expand \\n-separated ACL string to real newlines for assertions."""
        return acls.replace("\\n", "\n")

    def test_normal_domains_produce_dstdomain(self):
        acls = self._expand(
            format_domains_as_squid_acls(["oauth2.googleapis.com", ".example.com"])
        )
        assert "acl allowed_domains dstdomain oauth2.googleapis.com" in acls
        assert "acl allowed_domains dstdomain .example.com" in acls

    def test_regex_domains_produce_dstdom_regex(self):
        acls = self._expand(
            format_domains_as_squid_acls(
                ["oauth2.googleapis.com", "~aiplatform\\.googleapis\\.com$"]
            )
        )
        assert (
            "acl allowed_domains_regex dstdom_regex aiplatform\\.googleapis\\.com$"
            in acls
        )
        assert "acl allowed_domains dstdomain oauth2.googleapis.com" in acls

    def test_fallback_regex_acl_when_no_regex_domains(self):
        acls = self._expand(format_domains_as_squid_acls(["oauth2.googleapis.com"]))
        assert "acl allowed_domains_regex dstdom_regex ^$" in acls

    def test_no_fallback_when_regex_present(self):
        acls = format_domains_as_squid_acls(["~foo\\.com$"])
        assert "^$" not in acls

    def test_fallback_dstdomain_when_only_regex(self):
        """allowed_domains must always be defined, even with only regex domains."""
        acls = self._expand(format_domains_as_squid_acls(["~foo\\.com$"]))
        assert "acl allowed_domains dstdomain" in acls

    def test_no_mixed_types(self):
        """ACL names must not mix dstdomain and dstdom_regex."""
        acls = self._expand(
            format_domains_as_squid_acls(
                [
                    "oauth2.googleapis.com",
                    "~aiplatform\\.googleapis\\.com$",
                    ".example.com",
                ]
            )
        )
        acl_types: dict[str, set[str]] = {}
        for line in acls.splitlines():
            m = re.match(r"^acl\s+(\S+)\s+(dstdomain|dstdom_regex)\s", line)
            if m:
                name, typ = m.group(1), m.group(2)
                acl_types.setdefault(name, set()).add(typ)
        for name, types in acl_types.items():
            assert len(types) == 1, f"ACL '{name}' mixes types {types}"

    def test_empty_list(self):
        acls = format_domains_as_squid_acls([])
        assert "acl allowed_domains_regex dstdom_regex ^$" in acls
        assert "acl allowed_domains dstdomain" in acls


@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="entrypoint.sh uses GNU sed; macOS BSD sed is incompatible",
)
class TestEntrypointACLInjection:
    """Test entrypoint.sh correctly injects pre-formatted ACLs from Python."""

    @pytest.fixture
    def run_entrypoint(self, tmp_path: Path):
        """Helper that runs the ACL injection block from entrypoint.sh."""

        def _run(domains: list[str]) -> str:
            config = tmp_path / "squid.conf"
            config.write_text(SQUID_CONF.read_text())

            acls = format_domains_as_squid_acls(domains)

            # Extract the ALLOWED_DOMAIN_ACLS block from entrypoint.sh
            entrypoint = ENTRYPOINT_SH.read_text()
            start = entrypoint.index('if [[ -n "${ALLOWED_DOMAIN_ACLS:-}" ]]; then')
            lines = entrypoint[start:].splitlines()
            depth = 0
            block_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("if "):
                    depth += 1
                block_lines.append(line)
                if stripped == "fi":
                    depth -= 1
                    if depth == 0:
                        break

            script = (
                f"#!/bin/bash\nset -e\n"
                f'CONFIG_FILE="{config}"\n'
                f"ALLOWED_DOMAIN_ACLS='{acls}'\n"
                f"export CONFIG_FILE ALLOWED_DOMAIN_ACLS\n\n"
            )
            script += "\n".join(block_lines) + '\ncat "$CONFIG_FILE"\n'

            result = subprocess.run(
                ["bash", "-e"],
                input=script,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"Script failed: {result.stderr}"
            return result.stdout

        return _run

    def test_regex_domain_uses_separate_acl_name(self, run_entrypoint):
        """Regex domains must use allowed_domains_regex, not allowed_domains."""
        output = run_entrypoint(
            ["oauth2.googleapis.com", "~aiplatform\\.googleapis\\.com$"]
        )
        for line in output.splitlines():
            if "dstdom_regex" in line and line.startswith("acl "):
                assert "allowed_domains_regex" in line

            if "dstdomain" in line and line.startswith("acl "):
                assert "allowed_domains " in line

    def test_fallback_regex_acl_when_no_regex_domains(self, run_entrypoint):
        """allowed_domains_regex must always be defined, even with no regex domains."""
        output = run_entrypoint(["oauth2.googleapis.com", "accounts.google.com"])
        regex_acl_lines = [
            line
            for line in output.splitlines()
            if line.startswith("acl allowed_domains_regex")
        ]
        assert len(regex_acl_lines) >= 1

    def test_no_mixed_types_in_generated_config(self, run_entrypoint):
        """Generated config must not mix dstdomain and dstdom_regex under same ACL name."""
        output = run_entrypoint(
            ["oauth2.googleapis.com", "~aiplatform\\.googleapis\\.com$", ".example.com"]
        )
        acl_types: dict[str, set[str]] = {}
        for line in output.splitlines():
            m = re.match(r"^acl\s+(\S+)\s+(dstdomain|dstdom_regex)\s", line)
            if m:
                name, typ = m.group(1), m.group(2)
                acl_types.setdefault(name, set()).add(typ)
        for name, types in acl_types.items():
            assert len(types) == 1, (
                f"Generated config: ACL '{name}' mixes types {types}"
            )
