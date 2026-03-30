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


def _extract_if_block(source: str, marker: str) -> str:
    """Extract a complete if/fi block from a shell script starting at marker."""
    start = source.index(marker)
    lines = source[start:].splitlines()
    depth = 0
    block_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("if "):
            depth += 1
        block_lines.append(line)
        if stripped == "fi":
            depth -= 1
            if depth == 0:
                break
    return "\n".join(block_lines)


def _run_entrypoint_blocks(
    tmp_path: Path,
    env_vars: dict[str, str],
    markers: list[str],
) -> str:
    """Run extracted entrypoint.sh blocks with given env vars against squid.conf.

    Copies the base squid.conf to tmp_path, extracts the specified if/fi blocks
    from entrypoint.sh, sets env vars, runs the script, and returns the
    resulting squid.conf content.
    """
    config = tmp_path / "squid.conf"
    config.write_text(SQUID_CONF.read_text())
    entrypoint = ENTRYPOINT_SH.read_text()

    script = f'#!/bin/bash\nset -e\nCONFIG_FILE="{config}"\n'
    for name, value in env_vars.items():
        script += f"{name}='{value}'\nexport {name}\n"
    for marker in markers:
        script += _extract_if_block(entrypoint, marker) + "\n\n"
    script += 'cat "$CONFIG_FILE"\n'

    result = subprocess.run(
        ["bash", "-e"],
        input=script,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return result.stdout


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
            acls = format_domains_as_squid_acls(domains)
            return _run_entrypoint_blocks(
                tmp_path,
                env_vars={"ALLOWED_DOMAIN_ACLS": acls},
                markers=['if [[ -n "${ALLOWED_DOMAIN_ACLS:-}" ]]; then'],
            )

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


@pytest.mark.skipif(
    platform.system() == "Darwin",
    reason="entrypoint.sh uses GNU sed; macOS BSD sed is incompatible",
)
class TestEntrypointOTELPortInjection:
    """Test entrypoint.sh correctly injects OTEL ports into squid ACLs."""

    @pytest.fixture
    def run_otel_injection(self, tmp_path: Path):
        """Helper that runs the OTEL port injection block from entrypoint.sh."""

        def _run(otel_ports: str, domain_acls: str | None = None) -> str:
            env_vars: dict[str, str] = {"ALLOWED_OTEL_PORTS": otel_ports}
            markers = ['if [[ -n "${ALLOWED_OTEL_PORTS:-}" ]]; then']
            if domain_acls is not None:
                env_vars["ALLOWED_DOMAIN_ACLS"] = domain_acls
                markers.insert(0, 'if [[ -n "${ALLOWED_DOMAIN_ACLS:-}" ]]; then')
            return _run_entrypoint_blocks(tmp_path, env_vars, markers)

        return _run

    @staticmethod
    def _get_acl_ports(output: str, acl_name: str) -> list[str]:
        """Extract port values for a given ACL name from squid config output."""
        return [
            line.split()[-1]
            for line in output.splitlines()
            if line.startswith(f"acl {acl_name} port")
        ]

    def test_single_port_added_to_safe_and_ssl_ports(self, run_otel_injection):
        """A single OTEL port should be added to both Safe_ports and SSL_ports."""
        output = run_otel_injection("4318")
        assert "4318" in self._get_acl_ports(output, "Safe_ports")
        assert "4318" in self._get_acl_ports(output, "SSL_ports")

    def test_multiple_ports(self, run_otel_injection):
        """Multiple comma-separated OTEL ports should all be injected."""
        output = run_otel_injection("4317,4318")
        safe_ports = self._get_acl_ports(output, "Safe_ports")
        assert "4317" in safe_ports
        assert "4318" in safe_ports

    def test_ports_with_spaces_trimmed(self, run_otel_injection):
        """Ports with surrounding spaces should be trimmed correctly."""
        output = run_otel_injection("4317, 4318")
        safe_ports = self._get_acl_ports(output, "Safe_ports")
        assert "4317" in safe_ports
        assert "4318" in safe_ports

    def test_existing_ports_preserved(self, run_otel_injection):
        """Existing Safe_ports (80, 443) and SSL_ports (443) must be preserved."""
        output = run_otel_injection("4318")
        safe_ports = self._get_acl_ports(output, "Safe_ports")
        assert "80" in safe_ports
        assert "443" in safe_ports
        ssl_ports = self._get_acl_ports(output, "SSL_ports")
        assert "443" in ssl_ports

    def test_combined_with_domain_acl_injection(self, run_otel_injection):
        """OTEL port injection should work after domain ACL injection."""
        domain_acls = format_domains_as_squid_acls(
            ["oauth2.googleapis.com", "~aiplatform\\.googleapis\\.com$"]
        )
        output = run_otel_injection("4318", domain_acls=domain_acls)

        assert "acl allowed_domains dstdomain oauth2.googleapis.com" in output
        assert "4318" in self._get_acl_ports(output, "Safe_ports")
        assert "4318" in self._get_acl_ports(output, "SSL_ports")
