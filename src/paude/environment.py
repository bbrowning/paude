"""Environment variable builder for paude containers."""

from __future__ import annotations

import os


def build_environment() -> dict[str, str]:
    """Build the environment variables to pass to the container.

    Passes through Vertex AI related environment variables.

    Returns:
        Dictionary of environment variables.
    """
    env: dict[str, str] = {}

    # Vertex AI / Google Cloud variables
    passthrough_vars = [
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
    ]

    for var in passthrough_vars:
        value = os.environ.get(var)
        if value:
            env[var] = value

    # CLOUDSDK_AUTH_* variables
    for key, value in os.environ.items():
        if key.startswith("CLOUDSDK_AUTH_"):
            env[key] = value

    return env


def build_proxy_environment(proxy_name: str) -> dict[str, str]:
    """Build environment variables for proxy configuration.

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
    }
