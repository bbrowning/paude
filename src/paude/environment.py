"""Environment variable builder for paude containers."""

from __future__ import annotations


def build_environment(agent_name: str = "claude") -> dict[str, str]:
    """Build the environment variables to pass to the container.

    Delegates to the agent's build_environment() method.

    Args:
        agent_name: Agent name to use for environment building.

    Returns:
        Dictionary of environment variables.
    """
    from paude.agents import get_agent

    agent = get_agent(agent_name)
    return agent.build_environment()


CA_CERT_ANCHOR = "/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"
CA_BUNDLE_PATH = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
CA_CERT_DIR = "/etc/pki/tls/certs"


def build_proxy_environment(proxy_name: str) -> dict[str, str]:
    """Build environment variables for proxy configuration.

    Includes proxy URL settings and CA certificate trust paths so that
    tools inside the agent container (Node.js, Python requests, curl, etc.)
    trust the paude-proxy MITM CA certificate.

    Args:
        proxy_name: Name of the proxy container.

    Returns:
        Dictionary of proxy environment variables.
    """
    proxy_url = f"http://{proxy_name}:3128"
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
        "NODE_EXTRA_CA_CERTS": CA_CERT_ANCHOR,
        "REQUESTS_CA_BUNDLE": CA_BUNDLE_PATH,
        "SSL_CERT_FILE": CA_BUNDLE_PATH,
        "SSL_CERT_DIR": CA_CERT_DIR,
        "CURL_CA_BUNDLE": CA_BUNDLE_PATH,
    }
