"""Shared utilities for paude backends."""

from __future__ import annotations

import base64
import ipaddress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.agents.base import Agent, AgentConfig
    from paude.backends.base import SessionConfig
    from paude.backends.podman.backend import PodmanBackend

# Labels used to identify paude sessions
PAUDE_LABEL_APP = "app=paude"
PAUDE_LABEL_SESSION = "paude.io/session-name"
PAUDE_LABEL_WORKSPACE = "paude.io/workspace"
PAUDE_LABEL_CREATED = "paude.io/created-at"
PAUDE_LABEL_AGENT = "paude.io/agent"
PAUDE_LABEL_DOMAINS = "paude.io/allowed-domains"
PAUDE_LABEL_PROXY_IMAGE = "paude.io/proxy-image"
PAUDE_LABEL_VERSION = "paude.io/version"
PAUDE_LABEL_GPU = "paude.io/gpu"
PAUDE_LABEL_YOLO = "paude.io/yolo"
PAUDE_LABEL_PROVIDER = "paude.io/provider"
PAUDE_LABEL_OTEL_PORTS = "paude.io/otel-ports"
PAUDE_LABEL_OTEL_ENDPOINT = "paude.io/otel-endpoint"

PROXY_BLOCKED_LOG_PATH = "/tmp/paude-proxy-blocked.log"  # noqa: S108

# Path where the proxy CA cert is injected into agent containers.
CA_CERT_CONTAINER_PATH = "/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"

# Custom CA bundle combining system CAs + proxy CA cert.
# Written to /tmp so no root is needed (works with OpenShift arbitrary UIDs).
CA_BUNDLE_PATH = "/tmp/paude-ca-bundle.pem"  # noqa: S108


def derive_agent_ip(proxy_ip: str) -> str:
    """Derive the expected agent container IP from the proxy IP.

    The agent container is the next host on the internal network
    after the proxy (e.g. proxy=10.89.0.2 → agent=10.89.0.3).
    Used for defense-in-depth source IP filtering.
    """
    return str(ipaddress.ip_address(proxy_ip) + 1)


# CA certificate polling constants (shared by Podman and OpenShift backends).
CA_CERT_POLL_INTERVAL = 1
CA_CERT_POLL_TIMEOUT = 30

# Sentinel value for credentials managed by paude-proxy.
# Agent containers see this instead of real API keys.
PROXY_MANAGED_CREDENTIAL = "paude-proxy-managed"  # noqa: S105

# Stub GCP ADC JSON that satisfies Google client library structure checks.
# The proxy handles real authentication.
STUB_ADC_JSON = (
    '{"type": "authorized_user",'
    ' "client_id": "paude-proxy-managed",'
    ' "client_secret": "paude-proxy-managed",'
    ' "refresh_token": "paude-proxy-managed"}'
)

# Environment variable name for passing GCP ADC JSON content to the proxy.
PROXY_GCP_ADC_ENV = "GCP_ADC_JSON"

# Python snippet executed inside containers to extract the OpenClaw auth token.
# Used by both Podman and OpenShift backends via exec.
OPENCLAW_AUTH_READER_SCRIPT = (
    "import json,sys,os\n"
    "try:\n"
    "  h=os.environ.get('HOME','/home/paude')\n"
    "  f=open(h+'/.openclaw/openclaw.json')\n"
    "  t=json.load(f).get('gateway',{}).get('auth',{}).get('token','')\n"
    "  print(t) if t else sys.exit(1)\n"
    "except: sys.exit(1)"
)


def enrich_port_url(url: str, token: str | None) -> str:
    """Append an auth token fragment to a URL if a token is available."""
    return f"{url}/#token={token}" if token else url


def config_file_basename(config_file_name: str) -> str:
    """Strip leading dot from config file name.

    Example: '.claude.json' -> 'claude.json'
    """
    return config_file_name.lstrip(".")


def build_agent_env(config: AgentConfig) -> dict[str, str]:
    """Build agent env vars for container entrypoint parameterization."""
    env: dict[str, str] = {
        "PAUDE_AGENT_NAME": config.name,
        "PAUDE_AGENT_PROCESS": config.process_name,
        "PAUDE_AGENT_CONFIG_DIR": config.config_dir_name,
        "PAUDE_AGENT_INSTALL_SCRIPT": config.install_script,
        "PAUDE_AGENT_SESSION_NAME": config.session_name,
        "PAUDE_AGENT_LAUNCH_CMD": config.process_name,
    }
    env["PAUDE_AGENT_SEED_DIR"] = f"/tmp/{config.name}.seed"  # noqa: S108
    if config.config_file_name:
        basename = config_file_basename(config.config_file_name)
        env["PAUDE_AGENT_CONFIG_FILE"] = config.config_file_name
        env["PAUDE_AGENT_SEED_FILE"] = f"/tmp/{basename}.seed"  # noqa: S108
    else:
        env["PAUDE_AGENT_SEED_FILE"] = ""
    return env


def encode_path(path: Path, *, url_safe: bool = False) -> str:
    """Encode a path for storing in labels.

    Args:
        path: Path to encode.
        url_safe: Use URL-safe base64 encoding (for Podman labels).

    Returns:
        Base64-encoded path string.
    """
    encoder = base64.urlsafe_b64encode if url_safe else base64.b64encode
    return encoder(str(path).encode()).decode()


def decode_path(encoded: str, *, url_safe: bool = False) -> Path:
    """Decode a base64-encoded path.

    Args:
        encoded: Base64-encoded path string.
        url_safe: Use URL-safe base64 decoding (for Podman labels).

    Returns:
        Decoded Path object.
    """
    try:
        decoder = base64.urlsafe_b64decode if url_safe else base64.b64decode
        return Path(decoder(encoded.encode()).decode())
    except Exception:
        return Path(encoded)


def build_session_env(
    config: SessionConfig,
    agent: Agent,
    proxy_name: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Build environment variables and args for a session.

    Consolidates the duplicated env-building logic from Podman and OpenShift
    backends: agent env, YOLO flags, agent args, backward compat, proxy env,
    and prompt suppression.

    Args:
        config: Session configuration.
        agent: Resolved agent instance.
        proxy_name: Proxy container/service name (None if no proxy).

    Returns:
        Tuple of (env_dict, agent_args).
    """
    from paude.environment import build_proxy_environment

    env = dict(config.env)
    env.update(build_agent_env(agent.config))
    # build_agent_env sets LAUNCH_CMD to process_name, which is wrong for
    # agents where the launch binary differs from the process name (e.g.
    # OpenClaw: process_name="node" but launch is "openclaw gateway ...").
    # Override with the agent's actual launch command (no args — those are
    # passed separately via PAUDE_AGENT_ARGS).
    env["PAUDE_AGENT_LAUNCH_CMD"] = agent.launch_command("")

    agent_args = list(config.args)
    if config.yolo and agent.config.yolo_flag:
        agent_args = [agent.config.yolo_flag] + agent_args

    if agent_args:
        env[agent.config.args_env_var] = " ".join(agent_args)
    # Backward compat: also set PAUDE_CLAUDE_ARGS for existing containers
    if agent_args and agent.config.name == "claude":
        env["PAUDE_CLAUDE_ARGS"] = " ".join(agent_args)

    env["PAUDE_SUPPRESS_PROMPTS"] = "1"

    if proxy_name is not None:
        env.update(build_proxy_environment(proxy_name))
        # Set dummy credential values — real creds live in the proxy container
        for var in agent.config.secret_env_vars:
            env[var] = PROXY_MANAGED_CREDENTIAL
        env["GH_TOKEN"] = PROXY_MANAGED_CREDENTIAL

    return env, agent_args


# ---------------------------------------------------------------------------
# Resource naming helpers
# ---------------------------------------------------------------------------


def resource_name(session_name: str) -> str:
    """Get the resource name for a session (container, StatefulSet, git remote)."""
    return f"paude-{session_name}"


def proxy_resource_name(session_name: str) -> str:
    """Get the proxy resource name for a session (deployment, container, service)."""
    return f"paude-proxy-{session_name}"


def pod_name(session_name: str) -> str:
    """Get the pod name for a session (OpenShift StatefulSet pod)."""
    return f"paude-{session_name}-0"


def pvc_name(session_name: str) -> str:
    """Get the PVC name for a session (OpenShift workspace PVC)."""
    return f"workspace-paude-{session_name}-0"


def volume_name(session_name: str) -> str:
    """Get the volume name for a session (Podman volume)."""
    return f"paude-{session_name}-workspace"


def network_name(session_name: str) -> str:
    """Get the network name for a session (Podman network)."""
    return f"paude-net-{session_name}"


# Backend type helpers

LOCAL_BACKEND_TYPES = frozenset({"podman", "docker"})


def is_local_backend(backend_type: str) -> bool:
    """Check if a backend type is a local container engine (podman or docker)."""
    return backend_type in LOCAL_BACKEND_TYPES


def engine_binary_for_backend(backend_type: str) -> str:
    """Get the container engine binary for a backend type.

    Returns "podman" for "podman", "docker" for "docker".
    Raises ValueError for non-local backend types.
    """
    if backend_type in LOCAL_BACKEND_TYPES:
        return backend_type
    raise ValueError(f"No engine binary for backend type: {backend_type}")


def local_gcp_adc_path() -> Path | None:
    """Return the local GCP ADC file path, or None if it doesn't exist."""
    from paude.constants import GCP_ADC_FILENAME

    path = Path.home() / ".config" / "gcloud" / GCP_ADC_FILENAME
    return path if path.is_file() else None


def gather_proxy_credentials(
    agent_config: AgentConfig,
    *,
    gcp_adc_path: Path | None = None,
) -> dict[str, str]:
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
    import os

    from paude.agents.base import build_secret_environment_from_config

    creds = build_secret_environment_from_config(agent_config)

    gh_token = os.environ.get("PAUDE_GITHUB_TOKEN")
    if gh_token:
        creds["GH_TOKEN"] = gh_token

    if gcp_adc_path is not None:
        creds[PROXY_GCP_ADC_ENV] = gcp_adc_path.read_text()

    return creds


def generate_sandbox_config_script(
    agent_name: str,
    workspace: str,
    args: str,
    provider: str | None = None,
    *,
    yolo: bool = False,
) -> str:
    """Generate the sandbox config bash script for an agent."""
    from paude.agents import get_agent
    from paude.constants import CONTAINER_HOME

    agent = get_agent(agent_name, provider=provider)
    return agent.apply_sandbox_config(CONTAINER_HOME, workspace, args, yolo=yolo)


def build_ssh_backend(
    entry: object,
    connect_timeout: int | None = None,
) -> PodmanBackend | None:
    """Reconstruct a PodmanBackend with SSH transport from a registry entry.

    Args:
        entry: A RegistryEntry (or any object) to inspect.
        connect_timeout: SSH connect timeout in seconds. Uses default if None.

    Returns:
        PodmanBackend configured with SSH transport, or None on failure.
    """
    from paude.container.engine import ContainerEngine
    from paude.registry import RegistryEntry
    from paude.transport.ssh import SSH_CONNECT_TIMEOUT, SshTransport, parse_ssh_host

    if not isinstance(entry, RegistryEntry) or not entry.ssh_host:
        return None

    host, port = parse_ssh_host(entry.ssh_host)
    transport = SshTransport(
        host,
        key=entry.ssh_key,
        port=port,
        connect_timeout=connect_timeout or SSH_CONNECT_TIMEOUT,
    )
    engine = ContainerEngine(entry.engine, transport=transport)
    try:
        from paude.backends import PodmanBackend

        return PodmanBackend(engine=engine)
    except Exception:  # noqa: S110
        return None
