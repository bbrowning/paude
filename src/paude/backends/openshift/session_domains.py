"""Domain management for OpenShift sessions."""

from __future__ import annotations

from paude.backends.openshift.oc import OcClient
from paude.backends.openshift.proxy import ProxyManager
from paude.backends.openshift.session_lookup import SessionLookup
from paude.backends.shared import PAUDE_LABEL_SESSION, SQUID_BLOCKED_LOG_PATH


class SessionDomainManager:
    """Handles allowed-domain queries and updates for sessions."""

    def __init__(
        self,
        oc: OcClient,
        namespace: str,
        lookup: SessionLookup,
        proxy: ProxyManager,
    ) -> None:
        self._oc = oc
        self._namespace = namespace
        self._lookup = lookup
        self._proxy = proxy

    def get_allowed_domains(self, name: str) -> list[str] | None:
        """Get current allowed domains for a session.

        Returns:
            List of domains, or None if session has no proxy (unrestricted).

        Raises:
            SessionNotFoundError: If session not found.
        """
        self._lookup.require_session(name)

        if not self._lookup.has_proxy_deployment(name):
            return None

        return self._proxy.get_deployment_domains(name)

    def get_proxy_blocked_log(self, name: str) -> str | None:
        """Get raw squid blocked log from the proxy container.

        Returns:
            Raw log content, empty string if no blocks yet,
            or None if no proxy (unrestricted).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If proxy is not running.
        """
        self._lookup.require_session(name)

        if not self._lookup.has_proxy_deployment(name):
            return None

        pod_result = self._oc.run(
            "get",
            "pods",
            "-l",
            f"app=paude-proxy,{PAUDE_LABEL_SESSION}={name}",
            "-o",
            "jsonpath={.items[0].metadata.name}",
            "-n",
            self._namespace,
            check=False,
        )
        if pod_result.returncode != 0 or not pod_result.stdout.strip():
            raise ValueError(f"Proxy for session '{name}' is not running.")

        proxy_pod = pod_result.stdout.strip()
        log_result = self._oc.run(
            "exec",
            proxy_pod,
            "-n",
            self._namespace,
            "--",
            "cat",
            SQUID_BLOCKED_LOG_PATH,
            check=False,
        )
        if log_result.returncode != 0:
            return ""
        return log_result.stdout

    def update_allowed_domains(
        self,
        name: str,
        domains: list[str],
        credentials: dict[str, str] | None = None,
    ) -> None:
        """Update allowed domains for a session.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session has no proxy deployment.
        """
        self._lookup.require_session(name)

        if not self._lookup.has_proxy_deployment(name):
            raise ValueError(
                f"Session '{name}' has no proxy (unrestricted network). "
                "Cannot update domains."
            )

        self._proxy.update_deployment_domains(name, domains, credentials=credentials)
