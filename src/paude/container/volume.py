"""Volume management for paude."""

from __future__ import annotations

import json
import subprocess
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
        cmd = [self._engine.binary, "volume", "create"]
        if labels:
            for key, value in labels.items():
                cmd.extend(["--label", f"{key}={value}"])
        cmd.append(name)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        return result.stdout.strip()

    def remove_volume(self, name: str, force: bool = False) -> None:
        """Remove a named volume."""
        cmd = [self._engine.binary, "volume", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)

        subprocess.run(cmd, capture_output=True)

    def volume_exists(self, name: str) -> bool:
        """Check if a volume exists."""
        return self._engine.volume_exists(name)

    def get_volume_labels(self, name: str) -> dict[str, str]:
        """Get labels from a volume."""
        result = subprocess.run(
            [self._engine.binary, "volume", "inspect", "-f", "{{json .Labels}}", name],
            capture_output=True,
            text=True,
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
        cmd = [self._engine.binary, "volume", "ls", "--format", "json"]
        if label_filter:
            cmd.extend(["--filter", f"label={label_filter}"])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        try:
            return json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            return []
