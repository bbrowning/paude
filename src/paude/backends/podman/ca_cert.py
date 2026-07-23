"""CA certificate distribution for proxy-agent communication."""

from __future__ import annotations

import sys
import time

from paude.backends.podman.helpers import container_name, proxy_container_name
from paude.backends.shared import (
    CA_BUNDLE_PATH,
    CA_CERT_CONTAINER_PATH,
    CA_CERT_POLL_INTERVAL,
    CA_CERT_POLL_TIMEOUT,
    SYS_CA_BUNDLE_PATHS,
)
from paude.container.runner import ContainerRunner

_BUILD_CA_BUNDLE_CMD = (
    "SYS_BUNDLE=''; "
    f"for p in {' '.join(SYS_CA_BUNDLE_PATHS)}; do "
    '[ -f "$p" ] && SYS_BUNDLE="$p" && break; done; '
    f'[ -n "$SYS_BUNDLE" ] && cat "$SYS_BUNDLE" '
    f"{CA_CERT_CONTAINER_PATH} > {CA_BUNDLE_PATH}"
)


class CACertDistributor:
    """Distributes proxy CA certificates into agent containers."""

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    def distribute(self, session_name: str) -> None:
        """Copy the proxy's CA certificate into the agent container.

        Waits for the proxy to generate its CA cert at /data/ca/ca.crt,
        then copies it into the agent container's trust store and runs
        update-ca-trust.
        """
        pname = proxy_container_name(session_name)
        cname = container_name(session_name)

        if not self._runner.container_running(pname):
            return
        if not self._runner.container_running(cname):
            return

        elapsed = 0
        while elapsed < CA_CERT_POLL_TIMEOUT:
            result = self._runner.exec_in_container(
                pname, ["test", "-f", "/data/ca/ca.crt"], check=False
            )
            if result.returncode == 0:
                break
            time.sleep(CA_CERT_POLL_INTERVAL)
            elapsed += CA_CERT_POLL_INTERVAL
        else:
            print(
                "WARNING: Timed out waiting for proxy CA certificate.",
                file=sys.stderr,
            )
            return

        result = self._runner.exec_in_container(
            pname, ["cat", "/data/ca/ca.crt"], check=False
        )
        if result.returncode != 0 or not result.stdout.strip():
            print(
                "WARNING: Failed to read CA certificate from proxy.",
                file=sys.stderr,
            )
            return

        self._runner.inject_file(
            cname,
            result.stdout,
            CA_CERT_CONTAINER_PATH,
            owner="root:0",
            mode="644",
        )
        bundle_result = self._runner.exec_in_container(
            cname, ["sh", "-c", _BUILD_CA_BUNDLE_CMD], check=False
        )
        if bundle_result.returncode != 0:
            print(
                "WARNING: Failed to build custom CA bundle in agent container.",
                file=sys.stderr,
            )

    def redistribute_if_needed(self, session_name: str) -> None:
        """Verify the CA cert is still valid after a proxy recreate.

        The named CA volume persists across container removal, so the
        same cert should be reused. This method checks that the cert
        is present in the recreated proxy and that the agent container
        still has a matching cert. If the agent cert is missing or
        differs, it injects the cert directly (without re-polling).
        """
        pname = proxy_container_name(session_name)
        cname = container_name(session_name)

        if not self._runner.container_running(pname):
            return
        if not self._runner.container_running(cname):
            return

        elapsed = 0
        while elapsed < CA_CERT_POLL_TIMEOUT:
            result = self._runner.exec_in_container(
                pname, ["test", "-f", "/data/ca/ca.crt"], check=False
            )
            if result.returncode == 0:
                break
            time.sleep(CA_CERT_POLL_INTERVAL)
            elapsed += CA_CERT_POLL_INTERVAL
        else:
            print(
                "WARNING: CA certificate missing after proxy recreate.",
                file=sys.stderr,
            )
            return

        proxy_cert = self._runner.exec_in_container(
            pname, ["cat", "/data/ca/ca.crt"], check=False
        )
        if proxy_cert.returncode != 0 or not proxy_cert.stdout.strip():
            return

        agent_cert = self._runner.exec_in_container(
            cname, ["cat", CA_CERT_CONTAINER_PATH], check=False
        )

        if agent_cert.returncode != 0 or agent_cert.stdout != proxy_cert.stdout:
            print(
                "Redistributing CA certificate after proxy recreate...",
                file=sys.stderr,
            )
            self._runner.inject_file(
                cname,
                proxy_cert.stdout,
                CA_CERT_CONTAINER_PATH,
                owner="root:0",
                mode="644",
            )
            bundle_result = self._runner.exec_in_container(
                cname, ["sh", "-c", _BUILD_CA_BUNDLE_CMD], check=False
            )
            if bundle_result.returncode != 0:
                print(
                    "WARNING: Failed to build custom CA bundle in agent container.",
                    file=sys.stderr,
                )
