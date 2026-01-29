"""Proxy deployment and network policies for OpenShift backend."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from paude.backends.openshift.oc import OcClient


class ProxyManager:
    """Manages proxy deployments and network policies for sessions.

    This class handles creating and managing squid proxy pods, services,
    and associated network policies for domain-based traffic filtering.
    """

    def __init__(self, oc: OcClient, namespace: str) -> None:
        """Initialize the ProxyManager.

        Args:
            oc: OcClient instance for running oc commands.
            namespace: Kubernetes namespace for operations.
        """
        self._oc = oc
        self._namespace = namespace

    def ensure_network_policy(self, session_id: str) -> None:
        """Ensure a NetworkPolicy exists that restricts egress for this session.

        Creates a NetworkPolicy that:
        - Allows egress to DNS (UDP/TCP 53)
        - Allows egress to this session's proxy pod on port 3128
        - Denies all other egress traffic

        The paude pod can ONLY reach DNS and the squid proxy. The proxy handles
        domain-based filtering via squid.conf.

        Args:
            session_id: The session ID to scope the policy to.
        """
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace "
            f"{self._namespace}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": session_id,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    # Allow DNS to any pod in any namespace
                    {
                        "to": [
                            {
                                "namespaceSelector": {},
                                "podSelector": {},
                            },
                        ],
                        "ports": [
                            {"protocol": "UDP", "port": 53},
                            {"protocol": "TCP", "port": 53},
                            {"protocol": "UDP", "port": 5353},
                            {"protocol": "TCP", "port": 5353},
                        ],
                    },
                    # Allow access to THIS session's proxy pod only
                    {
                        "to": [
                            {
                                "podSelector": {
                                    "matchLabels": {
                                        "app": "paude-proxy",
                                        "paude.io/session-name": session_id,
                                    },
                                },
                            },
                        ],
                        "ports": [
                            {"protocol": "TCP", "port": 3128},
                        ],
                    },
                ],
            },
        }

        self._oc.run(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def ensure_network_policy_permissive(self, session_id: str) -> None:
        """Ensure a permissive NetworkPolicy exists for this session.

        Used when --allow-network is specified. Allows all egress traffic.

        Args:
            session_id: The session ID to scope the policy to.
        """
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace "
            f"{self._namespace}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude",
                    "session-id": session_id,
                    "paude.io/session-name": session_id,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude",
                        "paude.io/session-name": session_id,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    {},  # Empty rule allows all egress
                ],
            },
        }

        self._oc.run(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def ensure_proxy_network_policy(self, session_name: str) -> None:
        """Create a NetworkPolicy that allows all egress for the proxy pod.

        The proxy pod needs unrestricted egress to reach the internet.
        Domain-based filtering is handled by squid.conf, not NetworkPolicy.

        Args:
            session_name: Session name for labeling.
        """
        policy_name = f"paude-proxy-egress-{session_name}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace "
            f"{self._namespace}...",
            file=sys.stderr,
        )

        policy_spec: dict[str, Any] = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": policy_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app": "paude-proxy",
                        "paude.io/session-name": session_name,
                    },
                },
                "policyTypes": ["Egress"],
                "egress": [
                    {},  # Empty rule allows all egress
                ],
            },
        }

        self._oc.run(
            "apply", "-f", "-",
            input_data=json.dumps(policy_spec),
        )

    def create_deployment(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None = None,
    ) -> None:
        """Create a Deployment for the squid proxy pod.

        The proxy pod handles domain-based filtering using squid.conf.
        The paude container routes all HTTP/HTTPS traffic through this proxy.

        Args:
            session_name: Session name for labeling.
            proxy_image: Container image for the proxy.
            allowed_domains: List of domains to allow through the proxy.
        """
        deployment_name = f"paude-proxy-{session_name}"

        print(
            f"Creating Deployment/{deployment_name} in namespace "
            f"{self._namespace}...",
            file=sys.stderr,
        )

        env_list: list[dict[str, str]] = []
        if allowed_domains:
            domains_str = ",".join(allowed_domains)
            env_list.append({"name": "ALLOWED_DOMAINS", "value": domains_str})

        deployment_spec: dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": deployment_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {
                        "app": "paude-proxy",
                        "paude.io/session-name": session_name,
                    },
                },
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "paude-proxy",
                            "paude.io/session-name": session_name,
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "proxy",
                                "image": proxy_image,
                                "imagePullPolicy": "Always",
                                "ports": [{"containerPort": 3128}],
                                "env": env_list,
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "500m", "memory": "256Mi"},
                                },
                            },
                        ],
                    },
                },
            },
        }

        self._oc.run(
            "apply", "-f", "-",
            input_data=json.dumps(deployment_spec),
        )

    def create_service(self, session_name: str) -> str:
        """Create a Service for the squid proxy pod.

        Args:
            session_name: Session name for labeling.

        Returns:
            The service name (e.g., "paude-proxy-{session_name}").
        """
        service_name = f"paude-proxy-{session_name}"

        print(
            f"Creating Service/{service_name} in namespace {self._namespace}...",
            file=sys.stderr,
        )

        service_spec: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": service_name,
                "namespace": self._namespace,
                "labels": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
            },
            "spec": {
                "selector": {
                    "app": "paude-proxy",
                    "paude.io/session-name": session_name,
                },
                "ports": [
                    {
                        "port": 3128,
                        "targetPort": 3128,
                        "protocol": "TCP",
                    },
                ],
            },
        }

        self._oc.run(
            "apply", "-f", "-",
            input_data=json.dumps(service_spec),
        )

        return service_name

    def wait_for_ready(self, session_name: str, timeout: int = 120) -> None:
        """Wait for the proxy deployment to be ready.

        Args:
            session_name: Session name.
            timeout: Timeout in seconds.
        """
        deployment_name = f"paude-proxy-{session_name}"

        print(
            f"Waiting for Deployment/{deployment_name} to be ready...",
            file=sys.stderr,
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._oc.run(
                "get", "deployment", deployment_name,
                "-n", self._namespace,
                "-o", "jsonpath={.status.readyReplicas}",
                check=False,
            )

            if result.returncode == 0:
                ready = result.stdout.strip()
                if ready and int(ready) > 0:
                    print(
                        f"Deployment/{deployment_name} is ready.",
                        file=sys.stderr,
                    )
                    return

            time.sleep(2)

        print(
            f"Warning: Deployment/{deployment_name} not ready after {timeout}s",
            file=sys.stderr,
        )

    def delete_resources(self, session_name: str) -> None:
        """Delete proxy Deployment and Service for a session.

        Args:
            session_name: Session name.
        """
        deployment_name = f"paude-proxy-{session_name}"
        service_name = f"paude-proxy-{session_name}"

        print(f"Deleting Deployment/{deployment_name}...", file=sys.stderr)
        self._oc.run(
            "delete", "deployment", deployment_name,
            "-n", self._namespace,
            "--grace-period=0",
            check=False,
        )

        print(f"Deleting Service/{service_name}...", file=sys.stderr)
        self._oc.run(
            "delete", "service", service_name,
            "-n", self._namespace,
            check=False,
        )
