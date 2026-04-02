"""CA certificate generation and Kubernetes Secret management."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
from typing import Any

from paude.backends.openshift.oc import OcClient


def ca_secret_name(session_name: str) -> str:
    """Return the Kubernetes Secret name for a session's CA cert."""
    return f"paude-proxy-ca-{session_name}"


def creds_secret_name(session_name: str) -> str:
    """Return the Kubernetes Secret name for a session's proxy credentials."""
    return f"paude-proxy-creds-{session_name}"


def generate_ca_cert() -> tuple[str, str]:
    """Generate a self-signed CA certificate and private key.

    Uses ``openssl`` via subprocess.

    Returns:
        Tuple of (cert_pem, key_pem) as strings.

    Raises:
        RuntimeError: If openssl is not available or cert generation fails.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = f"{tmpdir}/ca.crt"
        key_path = f"{tmpdir}/ca.key"
        try:
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    key_path,
                    "-out",
                    cert_path,
                    "-days",
                    "3650",
                    "-nodes",
                    "-subj",
                    "/CN=paude-proxy-ca",
                ],
                capture_output=True,
                check=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "openssl is required to generate CA certificates but was not "
                "found on PATH. Install openssl and try again."
            ) from None
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"CA certificate generation failed: {exc.stderr}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("CA certificate generation timed out") from exc

        with open(cert_path) as f:
            cert_pem = f.read()
        with open(key_path) as f:
            key_pem = f.read()

    return cert_pem, key_pem


def create_ca_secret(
    oc: OcClient,
    namespace: str,
    session_name: str,
    cert_pem: str,
    key_pem: str,
) -> str:
    """Create a Kubernetes Secret containing the CA cert and key.

    Args:
        oc: OcClient instance.
        namespace: Kubernetes namespace.
        session_name: Session name for labeling.
        cert_pem: PEM-encoded CA certificate.
        key_pem: PEM-encoded CA private key.

    Returns:
        The Secret name.
    """
    secret_name = ca_secret_name(session_name)

    print(
        f"Creating Secret/{secret_name} in namespace {namespace}...",
        file=sys.stderr,
    )

    secret_spec: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "app": "paude-proxy",
                "paude.io/session-name": session_name,
            },
        },
        "type": "Opaque",
        "data": {
            "ca.crt": base64.b64encode(cert_pem.encode()).decode(),
            "ca.key": base64.b64encode(key_pem.encode()).decode(),
        },
    }

    oc.run("apply", "-f", "-", input_data=json.dumps(secret_spec))
    return secret_name


def create_credentials_secret(
    oc: OcClient,
    namespace: str,
    session_name: str,
    credentials: dict[str, str],
) -> str:
    """Create a Kubernetes Secret for proxy credentials.

    Args:
        oc: OcClient instance.
        namespace: Kubernetes namespace.
        session_name: Session name for labeling.
        credentials: Key-value pairs of credential env vars.

    Returns:
        The Secret name.
    """
    secret_name = creds_secret_name(session_name)

    print(
        f"Creating Secret/{secret_name} in namespace {namespace}...",
        file=sys.stderr,
    )

    secret_spec: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "app": "paude-proxy",
                "paude.io/session-name": session_name,
            },
        },
        "type": "Opaque",
        "data": {
            k: base64.b64encode(v.encode()).decode() for k, v in credentials.items()
        },
    }

    oc.run("apply", "-f", "-", input_data=json.dumps(secret_spec))
    return secret_name


def delete_secrets(
    oc: OcClient,
    namespace: str,
    session_name: str,
) -> None:
    """Delete CA and credential Secrets for a session.

    Args:
        oc: OcClient instance.
        namespace: Kubernetes namespace.
        session_name: Session name.
    """
    for name in (ca_secret_name(session_name), creds_secret_name(session_name)):
        oc.run(
            "delete",
            "secret",
            name,
            "-n",
            namespace,
            check=False,
        )
