"""Tests for session registry."""

from __future__ import annotations

import json
from pathlib import Path

from paude.backends.base import Session
from paude.registry import (
    RegistryEntry,
    SessionRegistry,
    merge_registry_with_live,
)


def _make_session(
    name: str,
    status: str = "running",
    backend_type: str = "podman",
    workspace: Path | None = None,
    agent: str = "claude",
    created_at: str = "2026-03-23T10:00:00Z",
) -> Session:
    return Session(
        name=name,
        status=status,
        workspace=workspace or Path(f"/home/user/{name}"),
        created_at=created_at,
        backend_type=backend_type,
        agent=agent,
    )


class TestSessionRegistry:
    """Tests for SessionRegistry."""

    def test_load_empty_when_file_missing(self, tmp_path: Path) -> None:
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        assert registry.load() == {}

    def test_load_empty_when_file_corrupt(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        path.write_text("not json{{{")
        registry = SessionRegistry(path=path)
        assert registry.load() == {}

    def test_register_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("test-session")

        registry.register(session)

        entries = registry.load()
        assert "test-session" in entries
        entry = entries["test-session"]
        assert entry.name == "test-session"
        assert entry.backend_type == "podman"
        assert entry.workspace == str(session.workspace)
        assert entry.agent == "claude"

    def test_register_with_remote_config_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("ssh-session", backend_type="docker")

        registry.register(
            session,
            ssh_host="remote-host",
            ssh_key="/path/to/key",
            remote_config_dir="/tmp/paude-config-XXXX",
        )

        entry = registry.get("ssh-session")
        assert entry is not None
        assert entry.remote_config_dir == "/tmp/paude-config-XXXX"
        assert entry.ssh_host == "remote-host"
        assert entry.ssh_key == "/path/to/key"

    def test_remote_config_dir_defaults_to_none(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("local-session")

        registry.register(session)

        entry = registry.get("local-session")
        assert entry is not None
        assert entry.remote_config_dir is None

    def test_register_with_openshift_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("os-session", backend_type="openshift")

        registry.register(
            session,
            openshift_context="my-cluster",
            openshift_namespace="my-ns",
        )

        entry = registry.get("os-session")
        assert entry is not None
        assert entry.openshift_context == "my-cluster"
        assert entry.openshift_namespace == "my-ns"

    def test_unregister(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("to-delete")
        registry.register(session)

        registry.unregister("to-delete")

        assert registry.get("to-delete") is None

    def test_unregister_missing_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        # Should not raise
        registry.unregister("nonexistent")

    def test_get_returns_none_for_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        assert registry.get("nope") is None

    def test_list_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        registry.register(_make_session("s1"))
        registry.register(_make_session("s2"))

        entries = registry.list_entries()
        names = {e.name for e in entries}
        assert names == {"s1", "s2"}

    def test_register_overwrites_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        registry.register(_make_session("s1", backend_type="podman"))
        registry.register(_make_session("s1", backend_type="openshift"))

        entry = registry.get("s1")
        assert entry is not None
        assert entry.backend_type == "openshift"

    def test_atomic_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "sessions.json"
        registry = SessionRegistry(path=path)
        registry.register(_make_session("s1"))
        assert path.exists()

    def test_remote_config_dir_survives_serialization(self, tmp_path: Path) -> None:
        """remote_config_dir is persisted to JSON and loaded back."""
        path = tmp_path / "sessions.json"
        registry = SessionRegistry(path=path)
        session = _make_session("ssh-s", backend_type="docker")
        registry.register(
            session,
            ssh_host="host",
            remote_config_dir="/tmp/paude-config-abcd",
        )

        # Reload from disk
        registry2 = SessionRegistry(path=path)
        entry = registry2.get("ssh-s")
        assert entry is not None
        assert entry.remote_config_dir == "/tmp/paude-config-abcd"

    def test_load_handles_missing_fields_gracefully(self, tmp_path: Path) -> None:
        path = tmp_path / "sessions.json"
        data = {
            "sessions": {
                "s1": {
                    "name": "s1",
                    "backend_type": "podman",
                    "workspace": "/home/user/s1",
                    "agent": "claude",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            }
        }
        path.write_text(json.dumps(data))
        registry = SessionRegistry(path=path)
        entries = registry.load()
        assert "s1" in entries
        assert entries["s1"].openshift_context is None


class TestRegistryEntryToSession:
    """Tests for RegistryEntry.to_session conversion."""

    def test_converts_to_session(self) -> None:
        entry = RegistryEntry(
            name="test",
            backend_type="podman",
            workspace="/home/user/test",
            agent="claude",
            created_at="2026-03-23T10:00:00Z",
        )
        session = entry.to_session(status="unreachable")
        assert session.name == "test"
        assert session.status == "unreachable"
        assert session.workspace == Path("/home/user/test")
        assert session.backend_type == "podman"
        assert session.agent == "claude"


class TestMergeRegistryWithLive:
    """Tests for merge_registry_with_live."""

    def test_live_overrides_registry(self, tmp_path: Path) -> None:
        """When a session exists in both, live data wins."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        registry.register(_make_session("s1", status="running"))

        live = [_make_session("s1", status="stopped")]
        result = merge_registry_with_live(registry, live, {"podman"})

        assert len(result) == 1
        assert result[0].name == "s1"
        assert result[0].status == "stopped"

    def test_unreachable_from_registry(self, tmp_path: Path) -> None:
        """Registry-only session with unreachable backend gets status 'unreachable'."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        registry.register(_make_session("s1", backend_type="podman"))

        result = merge_registry_with_live(registry, [], set())

        assert len(result) == 1
        assert result[0].name == "s1"
        assert result[0].status == "unreachable"

    def test_stale_when_backend_reachable_but_session_gone(
        self, tmp_path: Path
    ) -> None:
        """Registry-only session with reachable backend gets status 'stale'."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        registry.register(_make_session("s1", backend_type="podman"))

        result = merge_registry_with_live(registry, [], {"podman"})

        assert len(result) == 1
        assert result[0].name == "s1"
        assert result[0].status == "stale"

    def test_live_only_included(self, tmp_path: Path) -> None:
        """Live-only sessions are included in the result."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        live = [_make_session("new-s", status="running")]

        result = merge_registry_with_live(registry, live, {"podman"})

        assert len(result) == 1
        assert result[0].name == "new-s"
        assert result[0].status == "running"

    def test_live_only_backfilled_into_registry(self, tmp_path: Path) -> None:
        """Live-only sessions get backfilled into the registry."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        live = [_make_session("new-s", status="running")]

        merge_registry_with_live(registry, live, {"podman"})

        entry = registry.get("new-s")
        assert entry is not None
        assert entry.name == "new-s"

    def test_mixed_scenario(self, tmp_path: Path) -> None:
        """Test with sessions in various states."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        registry.register(_make_session("live-match", backend_type="podman"))
        registry.register(_make_session("stale-one", backend_type="podman"))
        registry.register(_make_session("unreachable-one", backend_type="openshift"))

        live = [
            _make_session("live-match", status="running"),
            _make_session("brand-new", status="running"),
        ]

        result = merge_registry_with_live(registry, live, {"podman"})

        by_name = {s.name: s for s in result}
        assert len(by_name) == 4
        assert by_name["live-match"].status == "running"
        assert by_name["stale-one"].status == "stale"
        assert by_name["unreachable-one"].status == "unreachable"
        assert by_name["brand-new"].status == "running"

    def test_empty_registry_and_no_live(self, tmp_path: Path) -> None:
        """Returns empty list when both registry and live are empty."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        result = merge_registry_with_live(registry, [], set())
        assert result == []

    def test_no_duplicate_when_in_both(self, tmp_path: Path) -> None:
        """Session appearing in both registry and live should appear exactly once."""
        registry = SessionRegistry(path=tmp_path / "sessions.json")
        registry.register(_make_session("s1"))
        live = [_make_session("s1", status="stopped")]

        result = merge_registry_with_live(registry, live, {"podman"})

        assert len(result) == 1
