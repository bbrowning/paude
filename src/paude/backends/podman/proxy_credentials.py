"""Proxy credential secret management for Podman sessions."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field

from paude.backends.podman.helpers import proxy_secret_name, proxy_secret_prefix
from paude.backends.proxy_config import (
    PROXY_CHATGPT_AUTH_STATE_ENV,
    ProxyCredentials,
)
from paude.container.proxy_inspect import ProxyInspectionError, ProxyInspector
from paude.container.runner import ContainerRunner


@dataclass
class PreparedProxyCredentials:
    """Credential arguments plus the resources needed for commit or rollback."""

    credentials: ProxyCredentials
    secret_refs: list[str] = field(default_factory=list)
    staged_secrets: list[str] = field(default_factory=list)
    superseded_secrets: list[str] = field(default_factory=list)


class ProxyCredentialManager:
    """Manages podman secrets for proxy credential injection."""

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner
        self._inspector = ProxyInspector(runner)

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

    def prepare_update(
        self,
        session_name: str,
        proxy_name: str,
        refresh: ProxyCredentials,
        credential_targets: set[str],
        required_targets: set[str],
    ) -> PreparedProxyCredentials:
        """Preserve current bindings and overlay only explicit refresh values."""
        targets = credential_targets | set(refresh.environment)
        if self._runner.engine.supports_secrets:
            return self._prepare_podman_update(
                session_name, proxy_name, refresh, required_targets
            )

        environment = self._inspector.environment(proxy_name)
        preserved = {key: environment[key] for key in targets if key in environment}
        preserved.update(refresh.environment)
        self._require_targets(set(preserved), required_targets)
        return PreparedProxyCredentials(
            credentials=ProxyCredentials(
                environment=preserved,
                chatgpt_oauth_mode=refresh.chatgpt_oauth_mode,
            )
        )

    def _prepare_podman_update(
        self,
        session_name: str,
        proxy_name: str,
        refresh: ProxyCredentials,
        required_targets: set[str],
    ) -> PreparedProxyCredentials:
        refs = self._inspector.secret_refs(proxy_name)
        refs_by_target: dict[str, str] = {}
        names_by_target: dict[str, str] = {}
        for ref in refs:
            target = self._secret_target(ref)
            if target is None:
                continue
            if target in refs_by_target:
                raise ProxyInspectionError(
                    f"Proxy '{proxy_name}' has duplicate credential target {target}."
                )
            refs_by_target[target] = ref
            names_by_target[target] = ref.split(",", 1)[0]

        self._require_targets(
            set(refs_by_target) | set(refresh.environment), required_targets
        )

        staged: list[str] = []
        superseded: list[str] = []
        candidate_refs = [
            ref for ref in refs if self._secret_target(ref) not in refresh.environment
        ]
        try:
            for target, value in refresh.environment.items():
                secret_name = (
                    f"{proxy_secret_name(session_name, target)}-"
                    f"update-{secrets.token_hex(4)}"
                )
                self._runner.create_secret_from_value(secret_name, value)
                staged.append(secret_name)
                candidate_refs.append(f"{secret_name},type=env,target={target}")
                previous = names_by_target.get(target)
                if previous and previous.startswith(proxy_secret_prefix(session_name)):
                    superseded.append(previous)
        except Exception:
            for secret_name in staged:
                self._runner.remove_secret(secret_name)
            raise

        return PreparedProxyCredentials(
            credentials=ProxyCredentials(chatgpt_oauth_mode=refresh.chatgpt_oauth_mode),
            secret_refs=candidate_refs,
            staged_secrets=staged,
            superseded_secrets=superseded,
        )

    def rollback_update(self, prepared: PreparedProxyCredentials) -> None:
        """Remove generation secrets created for an update that did not commit."""
        for secret_name in prepared.staged_secrets:
            self._runner.remove_secret(secret_name)

    def commit_update(self, prepared: PreparedProxyCredentials) -> None:
        """Remove only the old bindings superseded by an explicit refresh."""
        for secret_name in prepared.superseded_secrets:
            self._runner.remove_secret(secret_name)

    @staticmethod
    def _secret_target(ref: str) -> str | None:
        for part in ref.split(","):
            if part.startswith("target="):
                target = part.partition("=")[2]
                return target or None
        return None

    @staticmethod
    def _require_targets(present: set[str], required: set[str]) -> None:
        missing = sorted(required - present)
        if missing:
            names = ", ".join(missing)
            raise ValueError(
                "Cannot update proxy domains because required credential "
                f"bindings are missing: {names}. Supply them and use "
                "--refresh-credentials."
            )
