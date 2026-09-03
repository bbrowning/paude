"""Tests for the container-label codec and the typed spec it produces.

These pin the parse that ``paude upgrade``, ``paude backup`` and ``paude list``
all used to implement separately, including the two distinctions that were
easy to lose when it was written three times: absent-vs-empty for the domains
label, and raw-vs-derived for credential providers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paude.backends.labels import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_AGENT_PROVIDERS,
    PAUDE_LABEL_CREATED,
    PAUDE_LABEL_DOMAINS,
    PAUDE_LABEL_ENDPOINTS,
    PAUDE_LABEL_GPU,
    PAUDE_LABEL_OTEL_ENDPOINT,
    PAUDE_LABEL_PROVIDER,
    PAUDE_LABEL_PROVIDERS,
    PAUDE_LABEL_PROXY_IMAGE,
    PAUDE_LABEL_VERSION,
    PAUDE_LABEL_WORKSPACE,
    PAUDE_LABEL_YOLO,
    SessionSpec,
    decode_json_label,
    encode_agent_providers,
    encode_json_label,
    encode_providers,
    normalize_agent_providers,
    parse_agent_providers_label,
    parse_domains_label,
    parse_providers_label,
    read_labels,
    spec_from_labels,
    workspace_from_labels,
)
from paude.backends.session_env import encode_path


class TestSpecFromLabels:
    """Reading a session's declared configuration back out of its labels."""

    def test_empty_labels_give_the_documented_defaults(self) -> None:
        assert spec_from_labels({}) == SessionSpec()

    def test_reads_every_label(self) -> None:
        spec = spec_from_labels(
            {
                PAUDE_LABEL_AGENT: "codex",
                PAUDE_LABEL_PROVIDER: "openai",
                PAUDE_LABEL_AGENT_PROVIDERS: encode_agent_providers(
                    [("codex", "openai"), ("claude", "vertex")]
                ),
                PAUDE_LABEL_PROVIDERS: encode_providers(["openai", "vertex"]),
                PAUDE_LABEL_GPU: "all",
                PAUDE_LABEL_YOLO: "1",
                PAUDE_LABEL_OTEL_ENDPOINT: "http://otel:4317",
                PAUDE_LABEL_DOMAINS: "github.com,pypi.org",
                PAUDE_LABEL_ENDPOINTS: "api.example.com:8443,10.0.0.1:8000",
                PAUDE_LABEL_PROXY_IMAGE: "paude-proxy:1.2.3",
            }
        )
        assert spec == SessionSpec(
            agent="codex",
            provider="openai",
            agent_providers=[("codex", "openai"), ("claude", "vertex")],
            credential_providers=["openai", "vertex"],
            gpu="all",
            yolo=True,
            otel_endpoint="http://otel:4317",
            allowed_domains=["github.com", "pypi.org"],
            allowed_endpoints=["api.example.com:8443", "10.0.0.1:8000"],
            proxy_image="paude-proxy:1.2.3",
        )

    @pytest.mark.parametrize(
        ("label", "attribute"),
        [
            (PAUDE_LABEL_GPU, "gpu"),
            (PAUDE_LABEL_OTEL_ENDPOINT, "otel_endpoint"),
            (PAUDE_LABEL_PROXY_IMAGE, "proxy_image"),
            (PAUDE_LABEL_PROVIDER, "provider"),
        ],
    )
    def test_empty_optional_labels_read_back_as_none(
        self, label: str, attribute: str
    ) -> None:
        """An empty value is normalized to None so it never reaches a manifest."""
        assert getattr(spec_from_labels({label: ""}), attribute) is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("1", True), ("0", False), ("true", False), ("", False)],
    )
    def test_yolo_is_only_the_literal_one(self, value: str, expected: bool) -> None:
        assert spec_from_labels({PAUDE_LABEL_YOLO: value}).yolo is expected

    def test_credential_providers_are_the_raw_label(self) -> None:
        """No derive-from-composition fallback: the spec records what was declared."""
        spec = spec_from_labels(
            {
                PAUDE_LABEL_AGENT: "claude",
                PAUDE_LABEL_AGENT_PROVIDERS: encode_agent_providers(
                    [("claude", "vertex")]
                ),
            }
        )
        assert spec.credential_providers == []


class TestDomainsLabel:
    """Absent and empty mean different things and must not collapse."""

    def test_absent_label_is_none(self) -> None:
        """A legacy session predating the always-on proxy; callers expand it."""
        assert parse_domains_label(None) is None
        assert spec_from_labels({}).allowed_domains is None

    def test_empty_label_is_an_empty_list(self) -> None:
        """A proxy session that allows nothing."""
        assert parse_domains_label("") == []
        assert spec_from_labels({PAUDE_LABEL_DOMAINS: ""}).allowed_domains == []

    def test_comma_separated_values_split(self) -> None:
        assert parse_domains_label("a.com,b.org") == ["a.com", "b.org"]


class TestWorkspaceLabel:
    """Decoded on demand, with no default applied here."""

    def test_absent_label_is_none(self) -> None:
        assert workspace_from_labels({}) is None

    def test_empty_label_is_none(self) -> None:
        assert workspace_from_labels({PAUDE_LABEL_WORKSPACE: ""}) is None

    def test_round_trips_an_encoded_path(self) -> None:
        workspace = Path("/home/user/my project")
        labels = {PAUDE_LABEL_WORKSPACE: encode_path(workspace, url_safe=True)}
        assert workspace_from_labels(labels) == workspace


class TestJsonLabelCodec:
    """Structured labels are base64 JSON, with a raw-JSON fallback for legacy."""

    def test_agent_providers_round_trip(self) -> None:
        specs = [("claude", "vertex"), ("codex", "chatgpt")]
        assert parse_agent_providers_label(encode_agent_providers(specs)) == specs

    def test_providers_round_trip(self) -> None:
        providers = ["vertex", "chatgpt"]
        assert parse_providers_label(encode_providers(providers)) == providers

    def test_providers_are_deduplicated(self) -> None:
        raw = encode_providers(["vertex", "chatgpt", "vertex"])
        assert parse_providers_label(raw) == ["vertex", "chatgpt"]

    def test_legacy_raw_json_still_decodes(self) -> None:
        """Labels predating the base64 encoding were plain JSON."""
        assert decode_json_label('[["claude", "vertex"]]') == [["claude", "vertex"]]
        assert parse_agent_providers_label('[["claude", "vertex"]]') == [
            ("claude", "vertex")
        ]

    @pytest.mark.parametrize(
        "raw", ["", "not-base64-or-json", encode_json_label("nope")]
    )
    def test_unparseable_labels_are_empty_not_fatal(self, raw: str) -> None:
        """A container we cannot fully read is still a container we can list."""
        assert parse_agent_providers_label(raw) == []
        assert parse_providers_label(raw) == []

    def test_malformed_pairs_are_skipped(self) -> None:
        raw = encode_json_label([["claude", "vertex"], ["codex"], "codex", [1, 2]])
        assert parse_agent_providers_label(raw) == [("claude", "vertex")]


class TestNormalizeAgentProviders:
    """The manifest-side normalizer is strict where the label parse is lenient."""

    def test_none_and_empty_are_empty(self) -> None:
        assert normalize_agent_providers(None) == []
        assert normalize_agent_providers([]) == []

    def test_json_lists_become_tuples(self) -> None:
        assert normalize_agent_providers([["claude", "vertex"]]) == [
            ("claude", "vertex")
        ]

    def test_tuples_pass_through(self) -> None:
        assert normalize_agent_providers([("claude", "vertex")]) == [
            ("claude", "vertex")
        ]

    @pytest.mark.parametrize(
        "value",
        [[["claude"]], ["claude"], [[1, 2]], [["a", "b", "c"]], "claude", {}],
        ids=["short-pair", "bare-string", "non-strings", "long-pair", "string", "dict"],
    )
    def test_malformed_input_raises(self, value: object) -> None:
        """A manifest is a contract; its loader translates this into its own error."""
        with pytest.raises(ValueError, match="agent_providers"):
            normalize_agent_providers(value)


class TestReadLabels:
    """The whole of one container fetch."""

    def test_reads_spec_and_identity_together(self) -> None:
        workspace = Path("/home/user/project")
        view = read_labels(
            {
                PAUDE_LABEL_AGENT: "claude",
                PAUDE_LABEL_WORKSPACE: encode_path(workspace, url_safe=True),
                PAUDE_LABEL_CREATED: "2026-01-01T00:00:00+00:00",
                PAUDE_LABEL_VERSION: "0.20.2",
            }
        )
        assert view.spec.agent == "claude"
        assert view.workspace == workspace
        assert view.created_at == "2026-01-01T00:00:00+00:00"
        assert view.version == "0.20.2"

    def test_bare_container_reads_back_empty(self) -> None:
        view = read_labels({})
        assert view.spec == SessionSpec()
        assert view.workspace is None
        assert view.created_at == ""
        assert view.version is None
