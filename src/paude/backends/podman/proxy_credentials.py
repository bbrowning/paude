"""Proxy credential secret management for Podman sessions."""

from __future__ import annotations

from collections.abc import Mapping

from paude.backends.podman.helpers import proxy_secret_name, proxy_secret_prefix
from paude.backends.proxy_config import (
    PROXY_CHATGPT_AUTH_STATE_ENV,
    ProxyCredentials,
)
from paude.container.runner import ContainerRunner


class ProxyCredentialManager:
    """Manages podman secrets for proxy credential injection."""

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    def create_secrets(
        self,
        session_name: str,
        credentials: ProxyCredentials | Mapping[str, str] | None,
    ) -> list[str]:
        """Create podman secrets for proxy credentials.

        Each credential is stored as a separate podman secret scoped to
        the session.  The returned list contains ``--secret`` flag values
        using ``type=env`` so the secret is injected as an environment
        variable inside the container without appearing in inspect output.

        Returns an empty list when the engine does not support secrets
        (Docker), causing the caller to fall back to ``-e`` env vars.
        """
        if credentials is None:
            return []
        if not isinstance(credentials, ProxyCredentials):
            credentials = ProxyCredentials(environment=dict(credentials))
        if not self._runner.engine.supports_secrets:
            return []

        secret_refs: list[str] = []
        for key, value in credentials.items():
            sname = proxy_secret_name(session_name, key)
            self._runner.create_secret_from_value(sname, value)
            secret_refs.append(f"{sname},type=env,target={key}")
        return secret_refs

    def credential_env(
        self,
        credentials: ProxyCredentials | Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Return extra plain (non-secret) env vars derived from credential signals.

        Currently only used to tell paude-proxy a session wants Codex
        ChatGPT-OAuth mode. This is a plain env var, not a secret, so it
        works identically on Podman and Docker.
        """
        if isinstance(credentials, ProxyCredentials) and credentials.chatgpt_oauth_mode:
            return {PROXY_CHATGPT_AUTH_STATE_ENV: "/data/auth/chatgpt-auth.json"}
        return {}

    def remove_secrets(self, session_name: str) -> None:
        """Remove all podman secrets for a session's proxy credentials."""
        names = self._runner.list_secrets_by_prefix(proxy_secret_prefix(session_name))
        for sname in names:
            self._runner.remove_secret(sname)
