"""Strict inspection of the credential state attached to a proxy container."""

from __future__ import annotations

import json

from paude.container.runner import ContainerRunner


class ProxyInspectionError(RuntimeError):
    """A proxy inspection failed or returned malformed data."""


class ProxyInspector:
    """Read proxy state without collapsing failures into absent values."""

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    def secret_refs(self, name: str) -> list[str]:
        """Return the exact ``--secret`` refs used to create a Podman proxy."""
        result = self._runner.engine.run(
            "inspect", "-f", "{{json .Config.CreateCommand}}", name, check=False
        )
        if result.returncode != 0:
            raise ProxyInspectionError(
                f"Could not inspect credential bindings for proxy '{name}': "
                f"{result.stderr.strip() or 'container inspect failed'}"
            )
        try:
            command = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProxyInspectionError(
                f"Proxy '{name}' has malformed create-command inspection data."
            ) from exc
        if not isinstance(command, list) or not all(
            isinstance(arg, str) for arg in command
        ):
            raise ProxyInspectionError(
                f"Proxy '{name}' has malformed create-command inspection data."
            )

        refs: list[str] = []
        index = 0
        while index < len(command):
            arg = command[index]
            if arg == "--secret":
                if index + 1 >= len(command):
                    raise ProxyInspectionError(
                        f"Proxy '{name}' has a malformed --secret binding."
                    )
                refs.append(command[index + 1])
                index += 2
                continue
            if arg.startswith("--secret="):
                refs.append(arg.partition("=")[2])
            index += 1
        return refs

    def environment(self, name: str) -> dict[str, str]:
        """Return a proxy's configured environment, failing on bad inspection."""
        result = self._runner.engine.run(
            "inspect", "-f", "{{json .Config.Env}}", name, check=False
        )
        if result.returncode != 0:
            raise ProxyInspectionError(
                f"Could not inspect credential bindings for proxy '{name}': "
                f"{result.stderr.strip() or 'container inspect failed'}"
            )
        try:
            entries = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProxyInspectionError(
                f"Proxy '{name}' has malformed environment inspection data."
            ) from exc
        if not isinstance(entries, list) or not all(
            isinstance(entry, str) and "=" in entry for entry in entries
        ):
            raise ProxyInspectionError(
                f"Proxy '{name}' has malformed environment inspection data."
            )
        return dict(entry.split("=", 1) for entry in entries)

    def running(self, name: str) -> bool:
        """Return whether the proxy is running, failing if state is unreadable."""
        result = self._runner.engine.run(
            "inspect", "-f", "{{.State.Running}}", name, check=False
        )
        state = result.stdout.strip()
        if result.returncode != 0 or state not in {"true", "false"}:
            raise ProxyInspectionError(
                f"Could not inspect running state for proxy '{name}'."
            )
        return state == "true"
