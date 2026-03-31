"""Proxy deployment and network policies for OpenShift backend."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from paude.backends.openshift.oc import OcClient
from paude.backends.shared import proxy_resource_name


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
            f"Creating NetworkPolicy/{policy_name} in namespace {self._namespace}...",
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
            "apply",
            "-f",
            "-",
            input_data=json.dumps(policy_spec),
        )

    def ensure_network_policy_permissive(self, session_id: str) -> None:
        """Ensure a permissive NetworkPolicy exists for this session.

        Used when --allowed-domains all is specified. Allows all egress traffic.

        Args:
            session_id: The session ID to scope the policy to.
        """
        policy_name = f"paude-egress-{session_id}"

        print(
            f"Creating NetworkPolicy/{policy_name} in namespace {self._namespace}...",
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
            "apply",
            "-f",
            "-",
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
            f"Creating NetworkPolicy/{policy_name} in namespace {self._namespace}...",
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
            "apply",
            "-f",
            "-",
            input_data=json.dumps(policy_spec),
        )

    def create_deployment(
        self,
        session_name: str,
        proxy_image: str,
        allowed_domains: list[str] | None = None,
        otel_ports: list[int] | None = None,
    ) -> None:
        """Create a Deployment for the squid proxy pod.

        The proxy pod handles domain-based filtering using squid.conf.
        The paude container routes all HTTP/HTTPS traffic through this proxy.

        Args:
            session_name: Session name for labeling.
            proxy_image: Container image for the proxy.
            allowed_domains: List of domains to allow through the proxy.
            otel_ports: Non-standard ports to allow for OTEL endpoints.
        """
        deployment_name = proxy_resource_name(session_name)

        print(
            f"Creating Deployment/{deployment_name} in namespace {self._namespace}...",
            file=sys.stderr,
        )

        env_list: list[dict[str, str]] = []
        if allowed_domains:
            from paude.domains import format_domains_as_squid_acls

            domains_str = ",".join(allowed_domains)
            env_list.append({"name": "ALLOWED_DOMAINS", "value": domains_str})
            acls = format_domains_as_squid_acls(allowed_domains)
            env_list.append({"name": "ALLOWED_DOMAIN_ACLS", "value": acls})
        if otel_ports:
            env_list.append(
                {
                    "name": "ALLOWED_OTEL_PORTS",
                    "value": ",".join(str(p) for p in otel_ports),
                }
            )

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
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "containers": [
                            {
                                "name": "proxy",
                                "image": proxy_image,
                                "imagePullPolicy": os.environ.get(
                                    "PAUDE_IMAGE_PULL_POLICY", "Always"
                                ),
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
            "apply",
            "-f",
            "-",
            input_data=json.dumps(deployment_spec),
        )

    def create_service(self, session_name: str) -> str:
        """Create a Service for the squid proxy pod.

        Args:
            session_name: Session name for labeling.

        Returns:
            The service name (e.g., "paude-proxy-{session_name}").
        """
        service_name = proxy_resource_name(session_name)

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
            "apply",
            "-f",
            "-",
            input_data=json.dumps(service_spec),
        )

        return service_name

    def wait_for_ready(self, session_name: str, timeout: int = 120) -> None:
        """Wait for the proxy deployment to be ready.

        Args:
            session_name: Session name.
            timeout: Timeout in seconds.
        """
        deployment_name = proxy_resource_name(session_name)

        print(
            f"Waiting for Deployment/{deployment_name} to be ready...",
            file=sys.stderr,
        )

        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self._oc.run(
                "get",
                "deployment",
                deployment_name,
                "-n",
                self._namespace,
                "-o",
                "jsonpath={.status.readyReplicas}",
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

    def get_deployment_domains(self, session_name: str) -> list[str]:
        """Get the current ALLOWED_DOMAINS from the proxy Deployment.

        Args:
            session_name: Session name.

        Returns:
            List of currently allowed domains.
        """
        deployment_name = proxy_resource_name(session_name)

        result = self._oc.run(
            "get",
            f"deployment/{deployment_name}",
            "-n",
            self._namespace,
            "-o",
            "jsonpath={.spec.template.spec.containers[0]"
            '.env[?(@.name=="ALLOWED_DOMAINS")].value}',
            check=False,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return []

        return [d for d in result.stdout.strip().split(",") if d]

    def _patch_deployment_container(
        self,
        session_name: str,
        container_fields: dict[str, Any],
    ) -> None:
        """Apply a strategic merge patch to the proxy container spec."""
        deployment_name = proxy_resource_name(session_name)
        patch = json.dumps(
            {
                "spec": {
                    "template": {
                        "spec": {"containers": [{"name": "proxy", **container_fields}]}
                    }
                }
            }
        )
        self._oc.run(
            "patch",
            f"deployment/{deployment_name}",
            "-n",
            self._namespace,
            "--type=strategic",
            f"-p={patch}",
        )

    def update_deployment_domains(
        self,
        session_name: str,
        domains: list[str],
        otel_ports: list[int] | None = None,
        image: str | None = None,
    ) -> None:
        """Update the ALLOWED_DOMAINS env var on the proxy Deployment.

        Args:
            session_name: Session name.
            domains: New list of allowed domains.
            otel_ports: Non-standard OTEL ports to allow.  ``None`` (default)
                leaves the existing ALLOWED_OTEL_PORTS unchanged; an empty
                list clears it.
            image: If provided, also update the container image in the same
                patch to avoid a double pod restart.
        """
        from paude.domains import format_domains_as_squid_acls

        domains_str = ",".join(domains)
        acls = format_domains_as_squid_acls(domains)

        env_entries: list[dict[str, str]] = [
            {
                "name": "ALLOWED_DOMAINS",
                "value": domains_str,
            },
            {
                "name": "ALLOWED_DOMAIN_ACLS",
                "value": acls,
            },
        ]
        if otel_ports is not None:
            env_entries.append(
                {
                    "name": "ALLOWED_OTEL_PORTS",
                    "value": ",".join(str(p) for p in otel_ports),
                }
            )

        container_fields: dict[str, Any] = {"env": env_entries}
        if image is not None:
            container_fields["image"] = image
        self._patch_deployment_container(session_name, container_fields)

    def update_deployment_image(
        self,
        session_name: str,
        image: str,
    ) -> None:
        """Update the container image on the proxy Deployment.

        Args:
            session_name: Session name.
            image: New container image reference.
        """
        self._patch_deployment_container(session_name, {"image": image})

    def delete_resources(self, session_name: str) -> None:
        """Delete proxy Deployment and Service for a session.

        Args:
            session_name: Session name.
        """
        deployment_name = proxy_resource_name(session_name)
        service_name = proxy_resource_name(session_name)

        print(f"Deleting Deployment/{deployment_name}...", file=sys.stderr)
        self._oc.run(
            "delete",
            "deployment",
            deployment_name,
            "-n",
            self._namespace,
            "--grace-period=0",
            check=False,
        )

        print(f"Deleting Service/{service_name}...", file=sys.stderr)
        self._oc.run(
            "delete",
            "service",
            service_name,
            "-n",
            self._namespace,
            check=False,
        )
