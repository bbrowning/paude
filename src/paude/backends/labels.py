"""Container labels: the constants, their codec, and the typed spec they carry.

A session's container labels are the only durable record of how it was
configured. Three things need to read that record back -- ``paude upgrade``
(to rebuild the container), ``paude backup`` (to write a bundle manifest), and
``paude list`` (to describe a session) -- so the parse lives here, once,
instead of once per caller.

:class:`SessionSpec` is the typed form of that label set.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

PAUDE_LABEL_APP = "app=paude"
PAUDE_LABEL_SESSION = "paude.io/session-name"
PAUDE_LABEL_WORKSPACE = "paude.io/workspace"
PAUDE_LABEL_CREATED = "paude.io/created-at"
PAUDE_LABEL_AGENT = "paude.io/agent"
PAUDE_LABEL_DOMAINS = "paude.io/allowed-domains"
PAUDE_LABEL_ENDPOINTS = "paude.io/allowed-endpoints"
PAUDE_LABEL_PROXY_IMAGE = "paude.io/proxy-image"
PAUDE_LABEL_VERSION = "paude.io/version"
PAUDE_LABEL_GPU = "paude.io/gpu"
PAUDE_LABEL_YOLO = "paude.io/yolo"
PAUDE_LABEL_PROVIDER = "paude.io/provider"
PAUDE_LABEL_AGENT_PROVIDERS = "paude.io/agent-providers"
PAUDE_LABEL_PROVIDERS = "paude.io/providers"
PAUDE_LABEL_OTEL_PORTS = "paude.io/otel-ports"
PAUDE_LABEL_OTEL_ENDPOINT = "paude.io/otel-endpoint"


@dataclass(kw_only=True)
class SessionSpec:
    """The session configuration both durable manifests serialize.

    The field set is not a design principle, it is a schema constraint: these
    are exactly the fields ``UpgradeManifest`` and ``BackupManifest``
    already wrote to disk, kept verbatim so existing ``upgrades.json`` files
    and backup bundles stay readable. Adding a field here changes both
    schemas; that, not a rule about "declared configuration", is the test to
    apply. (``proxy_image`` is in it despite being a build output, and
    ``workspace`` is out despite being declared configuration -- both because
    of what was already on disk. ``workspace`` also has no single type here:
    the manifests store ``str`` where the rest of the code uses ``Path``.)

    Both manifests inherit this, so ``asdict()`` stays flat -- an embedded
    ``spec`` field would nest the JSON and strand in-flight upgrades written by
    an earlier version. ``kw_only`` is what makes that inheritance legal: every
    field here has a default, and both subclasses add required fields after
    them.
    """

    agent: str = "claude"
    provider: str | None = None
    agent_providers: list[tuple[str, str]] = field(default_factory=list)
    credential_providers: list[str] = field(default_factory=list)
    gpu: str | None = None
    yolo: bool = False
    otel_endpoint: str | None = None
    allowed_domains: list[str] | None = None
    allowed_endpoints: list[str] = field(default_factory=list)
    proxy_image: str | None = None


@dataclass(frozen=True)
class LabeledSession:
    """Everything one container's labels say about it.

    The unit of a single container fetch, which matters on SSH sessions where
    each fetch is a round trip.
    """

    spec: SessionSpec
    workspace: Path | None
    created_at: str
    version: str | None


def encode_json_label(value: Any) -> str:
    """Encode JSON as URL-safe base64 for use as a Podman label value."""
    payload = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_json_label(raw: str) -> Any | None:
    """Decode a structured label, accepting the original raw-JSON format."""
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        return json.loads(decoded)
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None


def encode_agent_providers(specs: list[tuple[str, str]]) -> str:
    """Encode ordered agent/provider pairs for a container label."""
    return encode_json_label(specs)


def encode_providers(providers: list[str]) -> str:
    """Encode a credential-provider set for a container label."""
    return encode_json_label(providers)


def parse_agent_providers_label(raw: str | None) -> list[tuple[str, str]]:
    """Parse a composition label, returning an empty list when invalid."""
    if not raw:
        return []
    value = decode_json_label(raw)
    if not isinstance(value, list):
        return []
    specs: list[tuple[str, str]] = []
    for item in value:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ):
            specs.append((item[0], item[1]))
    return specs


def parse_providers_label(raw: str | None) -> list[str]:
    """Parse a credential-provider label, returning empty when invalid."""
    if not raw:
        return []
    value = decode_json_label(raw)
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str)))


def parse_domains_label(raw: str | None) -> list[str] | None:
    """Parse the allowed-domains label, distinguishing absent from empty.

    ``None`` means the label was never written -- a legacy session created
    before every session got a proxy -- and callers expand it to the default
    domain set. ``[]`` means a proxy session that allows nothing, which the
    label records as an empty string.
    """
    if raw is None:
        return None
    return raw.split(",") if raw else []


def parse_endpoints_label(raw: str | None) -> list[str]:
    """Parse the endpoint label, defaulting legacy sessions to no exceptions."""
    return raw.split(",") if raw else []


def normalize_agent_providers(value: object) -> list[tuple[str, str]]:
    """Coerce a JSON-decoded agent/provider list back into tuples.

    JSON has no tuple type, so a round-tripped manifest reads its
    ``agent_providers`` back as lists. Unlike
    :func:`parse_agent_providers_label`, this is strict: a manifest is a
    contract, and a malformed one should surface as an error its loader can
    translate rather than as a silently truncated agent set.

    Raises:
        ValueError: If the value is not a list of two-string pairs.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"agent_providers must be a list, got {type(value).__name__}")
    specs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, list | tuple) or len(item) != 2:
            raise ValueError(f"agent_providers entry is not a pair: {item!r}")
        agent, provider = item
        if not isinstance(agent, str) or not isinstance(provider, str):
            raise ValueError(f"agent_providers entry is not two strings: {item!r}")
        specs.append((agent, provider))
    return specs


def workspace_from_labels(labels: Mapping[str, str]) -> Path | None:
    """Decode the workspace label, or ``None`` when it was never written.

    Deliberately applies no default: the two callers disagree about what a
    missing workspace means (``paude list`` shows ``/``; upgrade falls back to
    the current directory), and that disagreement is clearer at the call sites
    than hidden in here.
    """
    from paude.backends.session_env import decode_path

    encoded = labels.get(PAUDE_LABEL_WORKSPACE, "")
    return decode_path(encoded, url_safe=True) if encoded else None


def spec_from_labels(labels: Mapping[str, str]) -> SessionSpec:
    """Read a session's declared configuration out of its container labels.

    Empty label values are normalized to ``None``. paude only writes the
    optional labels when their value is truthy (see
    ``SessionSetup.build_session_labels``), so this is a type nicety rather
    than a behaviour change -- but it keeps ``""`` out of manifest JSON.

    ``credential_providers`` is the raw label, with no derive-from-composition
    fallback; that fallback belongs to callers describing a legacy session, not
    to the record of what was declared.
    """
    return SessionSpec(
        agent=labels.get(PAUDE_LABEL_AGENT, "claude"),
        provider=labels.get(PAUDE_LABEL_PROVIDER) or None,
        agent_providers=parse_agent_providers_label(
            labels.get(PAUDE_LABEL_AGENT_PROVIDERS)
        ),
        credential_providers=parse_providers_label(labels.get(PAUDE_LABEL_PROVIDERS)),
        gpu=labels.get(PAUDE_LABEL_GPU) or None,
        yolo=labels.get(PAUDE_LABEL_YOLO) == "1",
        otel_endpoint=labels.get(PAUDE_LABEL_OTEL_ENDPOINT) or None,
        allowed_domains=parse_domains_label(labels.get(PAUDE_LABEL_DOMAINS)),
        allowed_endpoints=parse_endpoints_label(labels.get(PAUDE_LABEL_ENDPOINTS)),
        proxy_image=labels.get(PAUDE_LABEL_PROXY_IMAGE) or None,
    )


def read_labels(labels: Mapping[str, str]) -> LabeledSession:
    """Read everything a container's labels record about its session."""
    return LabeledSession(
        spec=spec_from_labels(labels),
        workspace=workspace_from_labels(labels),
        created_at=labels.get(PAUDE_LABEL_CREATED, ""),
        version=labels.get(PAUDE_LABEL_VERSION),
    )
