"""Tests for squid proxy configuration consistency."""

from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

import pytest

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


@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="entrypoint.sh uses GNU sed; macOS BSD sed is incompatible",
)
class TestEntrypointRegexACL:
    """Test entrypoint.sh generates separate ACL names for regex domains."""

    @pytest.fixture
    def run_entrypoint(self, tmp_path: Path):
        """Helper that runs entrypoint domain-generation logic in a subprocess."""

        def _run(allowed_domains: str) -> str:
            """Run entrypoint.sh with ALLOWED_DOMAINS and return generated config."""
            # Copy squid.conf to tmp so entrypoint can modify it
            config = tmp_path / "squid.conf"
            config.write_text(SQUID_CONF.read_text())

            # Build a mini script that sources the domain-generation logic
            script = f"""\
#!/bin/bash
set -e
CONFIG_FILE="{config}"
ALLOWED_DOMAINS="{allowed_domains}"
export CONFIG_FILE ALLOWED_DOMAINS

# Inline the ALLOWED_DOMAINS block from entrypoint.sh
"""
            # Extract the ALLOWED_DOMAINS block from entrypoint.sh
            entrypoint = ENTRYPOINT_SH.read_text()
            # Find the block: if [[ -n "$ALLOWED_DOMAINS" ]]; then ... fi
            start = entrypoint.index('if [[ -n "$ALLOWED_DOMAINS" ]]; then')
            # Find matching fi (the block ends with a standalone fi line)
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
        """Regex domains (~prefix) must use allowed_domains_regex, not allowed_domains."""
        output = run_entrypoint("oauth2.googleapis.com,~aiplatform\\.googleapis\\.com$")

        # dstdom_regex must be under allowed_domains_regex
        for line in output.splitlines():
            if "dstdom_regex" in line and line.startswith("acl "):
                assert "allowed_domains_regex" in line, (
                    f"dstdom_regex line uses wrong ACL name: {line}"
                )

        # dstdomain must be under allowed_domains (not _regex)
        for line in output.splitlines():
            if "dstdomain" in line and line.startswith("acl "):
                assert "allowed_domains " in line, (
                    f"dstdomain line uses wrong ACL name: {line}"
                )

    def test_fallback_regex_acl_when_no_regex_domains(self, run_entrypoint):
        """allowed_domains_regex must always be defined, even with no regex domains."""
        output = run_entrypoint("oauth2.googleapis.com,accounts.google.com")

        # Must have an allowed_domains_regex ACL (fallback no-match)
        regex_acl_lines = [
            line
            for line in output.splitlines()
            if line.startswith("acl allowed_domains_regex")
        ]
        assert len(regex_acl_lines) >= 1, (
            "allowed_domains_regex ACL must be defined even without regex domains"
        )

    def test_no_mixed_types_in_generated_config(self, run_entrypoint):
        """Generated config must not mix dstdomain and dstdom_regex under same ACL name."""
        output = run_entrypoint(
            "oauth2.googleapis.com,~aiplatform\\.googleapis\\.com$,.example.com"
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
