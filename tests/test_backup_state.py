"""Tests for the backup bundle manifest (serialize/deserialize + version gating)."""

from __future__ import annotations

import json

import pytest

from paude import backup_state
from paude.backup_state import BACKUP_FORMAT_VERSION, BackupFormatError, BackupManifest


def _manifest(name: str = "test-session") -> BackupManifest:
    return BackupManifest(
        name=name,
        workspace="/home/user/project",
        created_at="2026-08-10T10:15:00+00:00",
        source_paude_version="0.20.0",
        session_created_at="2026-01-01T00:00:00+00:00",
        agent="claude",
        provider="anthropic",
        agent_providers=[("claude", "anthropic"), ("codex", "openai")],
        credential_providers=["anthropic", "openai"],
        gpu="all",
        yolo=True,
        otel_endpoint="http://collector:4318",
        allowed_domains=[".googleapis.com", ".pypi.org"],
        proxy_image="proxy:latest",
        image="runtime:abc123",
        backend_type="podman",
        engine="podman",
        ssh_host="user@box",
        ssh_key="/home/user/.ssh/id_ed25519",
        remote_config_dir="/tmp/paude-cfg",
        archive_sha256="deadbeef",
    )


class TestBackupManifest:
    def test_round_trip(self) -> None:
        manifest = _manifest()
        assert backup_state.loads(manifest.to_json()) == manifest

    def test_agent_providers_normalized_to_tuples(self) -> None:
        """JSON stores tuples as lists; loads restores tuples."""
        loaded = backup_state.loads(_manifest().to_json())
        assert loaded.agent_providers == [("claude", "anthropic"), ("codex", "openai")]
        assert all(isinstance(item, tuple) for item in loaded.agent_providers)

    def test_defaults_round_trip(self) -> None:
        """A minimal manifest (only required fields) round-trips too."""
        manifest = BackupManifest(
            name="s",
            workspace="/w",
            created_at="t",
            source_paude_version="v",
        )
        assert backup_state.loads(manifest.to_json()) == manifest

    def test_rejects_wrong_version(self) -> None:
        manifest = _manifest()
        manifest.backup_format_version = BACKUP_FORMAT_VERSION + 1
        with pytest.raises(BackupFormatError, match="Unsupported backup format"):
            backup_state.loads(manifest.to_json())

    def test_rejects_missing_version(self) -> None:
        with pytest.raises(BackupFormatError, match="Unsupported backup format"):
            backup_state.loads('{"name": "s"}')

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(BackupFormatError, match="not valid JSON"):
            backup_state.loads("{ not json")

    def test_rejects_non_object(self) -> None:
        with pytest.raises(BackupFormatError, match="not a JSON object"):
            backup_state.loads("[1, 2, 3]")

    def test_rejects_unexpected_fields(self) -> None:
        bad = f'{{"backup_format_version": {BACKUP_FORMAT_VERSION}, "bogus": 1}}'
        with pytest.raises(BackupFormatError, match="unexpected fields"):
            backup_state.loads(bad)


class TestManifestSchema:
    """Guards on the bundle schema, which older bundles depend on."""

    # A manifest exactly as paude 0.20.2 wrote it, before BackupManifest
    # inherited SessionSpec: flat, and in the field order of that release.
    LEGACY_JSON = """
    {
        "name": "test-session",
        "workspace": "/home/user/project",
        "created_at": "2026-08-10T10:15:00+00:00",
        "source_paude_version": "0.20.2",
        "backup_format_version": 1,
        "session_created_at": "2026-01-01T00:00:00+00:00",
        "archive_sha256": "deadbeef",
        "agent": "claude",
        "provider": "anthropic",
        "agent_providers": [["claude", "anthropic"]],
        "credential_providers": ["anthropic"],
        "gpu": null,
        "yolo": false,
        "otel_endpoint": null,
        "allowed_domains": [".pypi.org"],
        "proxy_image": "proxy:latest",
        "image": "runtime:abc123",
        "backend_type": "podman",
        "engine": "podman",
        "ssh_host": null,
        "ssh_key": null,
        "remote_config_dir": null
    }
    """

    def test_a_bundle_from_an_earlier_release_still_loads(self) -> None:
        loaded = backup_state.loads(self.LEGACY_JSON)

        assert loaded.name == "test-session"
        assert loaded.agent_providers == [("claude", "anthropic")]
        assert loaded.allowed_domains == [".pypi.org"]
        assert loaded.image == "runtime:abc123"
        assert loaded.gpu is None

    def test_serialized_json_stays_flat(self) -> None:
        """Nesting the spec would make every existing bundle unreadable."""
        entry = json.loads(_manifest().to_json())

        assert "spec" not in entry
        assert not any(isinstance(value, dict) for value in entry.values())
        assert set(entry) == {
            "name",
            "workspace",
            "created_at",
            "source_paude_version",
            "backup_format_version",
            "session_created_at",
            "archive_sha256",
            "agent",
            "provider",
            "agent_providers",
            "credential_providers",
            "gpu",
            "yolo",
            "otel_endpoint",
            "allowed_domains",
            "proxy_image",
            "image",
            "backend_type",
            "engine",
            "ssh_host",
            "ssh_key",
            "remote_config_dir",
        }

    @pytest.mark.parametrize(
        "agent_providers",
        ['[["claude"]]', '["claude"]', "[[1, 2]]", '"claude"'],
        ids=["short-pair", "bare-string", "non-strings", "string"],
    )
    def test_malformed_agent_providers_is_a_format_error(
        self, agent_providers: str
    ) -> None:
        """Better here than as a 1-tuple that blows up where a caller unpacks it."""
        text = (
            f'{{"backup_format_version": {BACKUP_FORMAT_VERSION}, '
            f'"name": "s", "workspace": "/w", "created_at": "t", '
            f'"source_paude_version": "v", "agent_providers": {agent_providers}}}'
        )
        with pytest.raises(BackupFormatError, match="malformed field"):
            backup_state.loads(text)
