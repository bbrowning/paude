"""Volume management for paude."""

from __future__ import annotations

import json
from typing import Any

from paude.container.engine import ContainerEngine


class VolumeManager:
    """Manages container volumes."""

    def __init__(self, engine: ContainerEngine | None = None) -> None:
        self._engine = engine or ContainerEngine()

    def create_volume(self, name: str, labels: dict[str, str] | None = None) -> str:
        """Create a named volume.

        Returns:
            Volume name.
        """
        args = ["volume", "create"]
        if labels:
            for key, value in labels.items():
                args.extend(["--label", f"{key}={value}"])
        args.append(name)

        result = self._engine.run(*args)
        return result.stdout.strip()

    def remove_volume(self, name: str, force: bool = False) -> None:
        """Remove a named volume."""
        args = ["volume", "rm"]
        if force:
            args.append("-f")
        args.append(name)

        self._engine.run(*args, check=False)

    def remove_volume_verified(self, name: str) -> None:
        """Remove a volume and verify it was actually removed.

        Raises:
            RuntimeError: If the volume still exists after removal.
        """
        self.remove_volume(name, force=True)
        if self.volume_exists(name):
            raise RuntimeError(
                f"Failed to remove volume '{name}' — it still exists after removal"
            )

    def volume_exists(self, name: str) -> bool:
        """Check if a volume exists."""
        return self._engine.volume_exists(name)

    def get_volume_labels(self, name: str) -> dict[str, str]:
        """Get labels from a volume."""
        result = self._engine.run(
            "volume", "inspect", "-f", "{{json .Labels}}", name, check=False
        )
        if result.returncode != 0:
            return {}

        try:
            labels = json.loads(result.stdout) if result.stdout.strip() else {}
            return labels if labels else {}
        except json.JSONDecodeError:
            return {}

    def list_volumes(
        self,
        label_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List volumes with optional label filter."""
        args = ["volume", "ls", "--format", "json"]
        if label_filter:
            args.extend(["--filter", f"label={label_filter}"])

        result = self._engine.run(*args, check=False)
        if result.returncode != 0:
            return []

        try:
            return json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            return []
