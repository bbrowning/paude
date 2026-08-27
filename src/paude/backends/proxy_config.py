"""Proxy and CA certificate configuration for paude backends."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentComposition, AgentConfig

PROXY_BLOCKED_LOG_PATH = "/tmp/paude-proxy-blocked.log"  # noqa: S108

CA_CERT_CONTAINER_PATH = "/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"

CA_BUNDLE_PATH = "/tmp/paude-ca-bundle.pem"  # noqa: S108

# System CA bundle paths across distros (RHEL/CentOS, Debian/Ubuntu,
# openSUSE, Alpine).  Keep in sync with _find_sys_ca_bundle() in
# containers/paude/entrypoint-lib-credentials.sh.
SYS_CA_BUNDLE_PATHS = (
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)

CA_CERT_POLL_INTERVAL = 1
CA_CERT_POLL_TIMEOUT = 30

PROXY_MANAGED_CREDENTIAL = "paude-proxy-managed"  # noqa: S105

STUB_ADC_JSON = (
    '{"type": "authorized_user",'
    ' "client_id": "paude-proxy-managed",'
    ' "client_secret": "paude-proxy-managed",'
    ' "refresh_token": "paude-proxy-managed"}'
)

PROXY_GCP_ADC_ENV = "GCP_ADC_JSON"

# Codex ChatGPT OAuth is fully proxy-managed and file-free on paude's side.
# paude only signals that a session wants ChatGPT-plan mode via
# PROXY_CHATGPT_AUTH_STATE_ENV; the user runs `codex login` inside the
# container, and paude-proxy captures, persists, and refreshes the resulting
# real tokens independently per session (see paude-proxy's ChatGPTInjector /
# TokenVendor). No host file is ever read or shared across sessions.
#
# paude never pre-seeds `codex login` state either: doing so made Codex
# believe it was already authenticated on a fresh session (skipping its own
# login prompt) while paude-proxy had no real tokens yet for that session.
# Codex's own login flow writes its auth.json itself (under CODEX_HOME,
# i.e. /home/paude/.codex, which is symlinked onto /pvc); paude-proxy swaps
# in synthetic values at the token exchange so real tokens never land in the
# agent container. paude's reset of that auth.json lives with the other codex
# on-volume paths as CODEX_AUTH_TARGET in agents/codex_config.py.
PROXY_CHATGPT_AUTH_STATE_ENV = "PAUDE_PROXY_CHATGPT_AUTH_STATE_FILE"


@dataclass
class ProxyCredentials(Mapping[str, str]):
    """Credentials and mode signals for a proxy container.

    ``environment`` contains ordinary proxy environment credentials.
    ``chatgpt_oauth_mode`` signals that this session wants Codex ChatGPT-plan
    OAuth support; no file credential is ever attached for it. Implementing
    the mapping protocol keeps existing environment-only backend consumers
    source-compatible.
    """

    environment: dict[str, str] = field(default_factory=dict)
    chatgpt_oauth_mode: bool = False

    def __getitem__(self, key: str) -> str:
        return self.environment[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.environment)

    def __len__(self) -> int:
        return len(self.environment)


# Python snippet executed inside containers to extract the OpenClaw auth token.
OPENCLAW_AUTH_READER_SCRIPT = (
    "import json,sys,os\n"
    "try:\n"
    "  h=os.environ.get('HOME','/home/paude')\n"
    "  f=open(h+'/.openclaw/openclaw.json')\n"
    "  t=json.load(f).get('gateway',{}).get('auth',{}).get('token','')\n"
    "  print(t) if t else sys.exit(1)\n"
    "except: sys.exit(1)"
)


def derive_agent_ip(proxy_ip: str) -> str:
    """Derive the expected agent container IP from the proxy IP.

    The agent container is the next host on the internal network
    after the proxy (e.g. proxy=10.89.0.2 → agent=10.89.0.3).
    Used for defense-in-depth source IP filtering.
    """
    return str(ipaddress.ip_address(proxy_ip) + 1)


def local_gcp_adc_path() -> Path | None:
    """Return the local GCP ADC file path, or None if it doesn't exist."""
    from paude.constants import GCP_ADC_FILENAME

    path = Path.home() / ".config" / "gcloud" / GCP_ADC_FILENAME
    return path if path.is_file() else None


def gather_proxy_credentials(
    agent_config: AgentConfig | AgentComposition | Agent,
    *,
    credential_providers: list[str] | None = None,
    gcp_adc_path: Path | None = None,
) -> ProxyCredentials:
    """Gather real credentials from the host for the proxy container.

    Reads secret env vars (API keys) and GH_TOKEN from the host
    environment. If a GCP ADC file exists locally, its content is
    passed as ``GCP_ADC_JSON`` so the proxy has it at startup.

    Args:
        agent_config: Agent configuration with secret_env_vars.
        gcp_adc_path: Path to local GCP ADC file, or None if absent.

    Returns:
        Dict of environment variables for the proxy container.
    """
    from paude.agents.base import build_secret_environment_from_config
    from paude.providers import get_provider

    if hasattr(agent_config, "agents"):
        configs = [agent.config for agent in agent_config.agents]
    elif hasattr(agent_config, "config"):
        configs = [agent_config.config]
    else:
        configs = [agent_config]

    creds: dict[str, str] = {}
    for config in configs:
        for key, value in build_secret_environment_from_config(config).items():
            creds.setdefault(key, value)

    effective_providers = credential_providers or list(
        dict.fromkeys(config.provider for config in configs if config.provider)
    )
    for provider_name in effective_providers:
        provider = get_provider(provider_name)
        for key in provider.secret_env_vars:
            env_value = os.environ.get(key)
            if env_value:
                creds.setdefault(key, env_value)

    gh_token = os.environ.get("PAUDE_GITHUB_TOKEN")
    if gh_token:
        creds["GH_TOKEN"] = gh_token

    if gcp_adc_path is not None and any(
        provider in {"vertex", "google"} for provider in effective_providers
    ):
        creds[PROXY_GCP_ADC_ENV] = gcp_adc_path.read_text()

    chatgpt_oauth_mode = "chatgpt" in effective_providers

    return ProxyCredentials(environment=creds, chatgpt_oauth_mode=chatgpt_oauth_mode)


def proxy_credential_targets(
    agent_config: AgentConfig | AgentComposition | Agent,
) -> set[str]:
    """Return every environment credential paude may bind to a proxy.

    Domain-only updates use this allow-list when reading Docker's inspected
    environment.  Keeping the list explicit prevents ordinary proxy settings
    from being replayed as credentials while still preserving credentials for
    providers other than the session's primary provider.
    """
    from paude.providers import get_provider, list_providers

    targets = {"GH_TOKEN", PROXY_GCP_ADC_ENV}
    targets.update(
        key
        for provider_name in list_providers()
        for key in get_provider(provider_name).secret_env_vars
    )
    targets.update(
        key for config in _agent_configs(agent_config) for key in config.secret_env_vars
    )
    return targets


def required_proxy_credential_targets(
    agent_config: AgentConfig | AgentComposition | Agent,
    credential_providers: list[str],
) -> set[str]:
    """Return credential targets required by the session's selected providers."""
    from paude.providers import get_provider

    required = {
        key
        for provider_name in credential_providers
        for key in get_provider(provider_name).secret_env_vars
    }
    required.update(
        key for config in _agent_configs(agent_config) for key in config.secret_env_vars
    )
    return required


def _agent_configs(
    agent_config: AgentConfig | AgentComposition | Agent,
) -> list[AgentConfig]:
    """Normalize an agent, composition, or config to its configs."""
    if hasattr(agent_config, "agents"):
        return [agent.config for agent in agent_config.agents]
    if hasattr(agent_config, "config"):
        return [agent_config.config]
    return [agent_config]
