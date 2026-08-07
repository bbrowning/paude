"""Tests for the durable upgrade manifest (crash-safe upgrade recovery)."""

from __future__ import annotations

from pathlib import Path

from paude import upgrade_state
from paude.upgrade_state import UpgradeManifest


def _manifest(name: str = "test-session") -> UpgradeManifest:
    return UpgradeManifest(
        name=name,
        to_version="0.20.0",
        created_at="2026-01-01T00:00:00+00:00",
        workspace="/home/user/project",
        agent="claude",
        provider="anthropic",
        agent_providers=[("claude", "anthropic"), ("codex", "openai")],
        credential_providers=["anthropic", "openai"],
        gpu="all",
        yolo=True,
        otel_endpoint="http://collector:4318",
        allowed_domains=[".googleapis.com", ".pypi.org"],
        proxy_image="proxy:latest",
    )


class TestUpgradeState:
    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        assert upgrade_state.load("nope", path=path) is None

    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        manifest = _manifest()
        upgrade_state.save(manifest, path=path)

        loaded = upgrade_state.load("test-session", path=path)
        assert loaded == manifest

    def test_agent_providers_normalised_to_tuples(self, tmp_path: Path) -> None:
        """JSON stores tuples as lists; load restores tuples for get_agents()."""
        path = tmp_path / "upgrades.json"
        upgrade_state.save(_manifest(), path=path)

        loaded = upgrade_state.load("test-session", path=path)
        assert loaded is not None
        assert loaded.agent_providers == [("claude", "anthropic"), ("codex", "openai")]
        assert all(isinstance(item, tuple) for item in loaded.agent_providers)

    def test_save_is_atomic_no_temp_left(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        upgrade_state.save(_manifest(), path=path)

        # No stray .tmp files remain from the atomic write.
        assert list(tmp_path.glob("*.tmp")) == []
        assert path.exists()

    def test_multiple_sessions_coexist(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        upgrade_state.save(_manifest("session-a"), path=path)
        upgrade_state.save(_manifest("session-b"), path=path)

        assert upgrade_state.load("session-a", path=path) is not None
        assert upgrade_state.load("session-b", path=path) is not None

    def test_delete_removes_only_target(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        upgrade_state.save(_manifest("session-a"), path=path)
        upgrade_state.save(_manifest("session-b"), path=path)

        upgrade_state.delete("session-a", path=path)

        assert upgrade_state.load("session-a", path=path) is None
        assert upgrade_state.load("session-b", path=path) is not None

    def test_delete_absent_is_noop(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        # Should not raise even when the file does not exist.
        upgrade_state.delete("nope", path=path)
        assert not path.exists()

    def test_corrupt_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "upgrades.json"
        path.write_text("{ this is not valid json")
        assert upgrade_state.load("test-session", path=path) is None

    def test_corrupt_entry_returns_none(self, tmp_path: Path) -> None:
        """An entry with unexpected fields is treated as corrupt, not crashed."""
        path = tmp_path / "upgrades.json"
        path.write_text('{"upgrades": {"test-session": {"unexpected": "field"}}}')
        assert upgrade_state.load("test-session", path=path) is None
