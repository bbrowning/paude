"""Durable mutable proxy configuration stored in the session auth volume."""

from __future__ import annotations

import json

from paude.container.runner import ContainerRunner

_STATE_PATH = "/data/auth/allowed-domains.json"
_STATE_SCHEMA = "allowed-domains.v1"
_MISSING_EXIT = 3


class ProxyStateError(RuntimeError):
    """Durable proxy state could not be read or written safely."""


class ProxyStateStore:
    """Read and atomically write non-secret proxy state through the engine."""

    def __init__(self, runner: ContainerRunner) -> None:
        self._runner = runner

    def read(self, volume: str, image: str) -> list[str] | None:
        """Return committed domains, or ``None`` for a legacy absent record."""
        result = self._runner.engine.run(
            "run",
            "--rm",
            "-v",
            f"{volume}:/data/auth:ro",
            "--entrypoint",
            "sh",
            image,
            "-c",
            f"test -e {_STATE_PATH} || exit {_MISSING_EXIT}; cat {_STATE_PATH}",
            check=False,
        )
        if result.returncode == _MISSING_EXIT:
            return None
        if result.returncode != 0:
            raise ProxyStateError(
                "Could not read durable allowed-domain state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )
        try:
            record = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProxyStateError("Durable allowed-domain state is corrupt.") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema") != _STATE_SCHEMA
            or not isinstance(record.get("domains"), list)
            or not all(isinstance(item, str) for item in record["domains"])
        ):
            raise ProxyStateError("Durable allowed-domain state is corrupt.")
        return list(record["domains"])

    def write(self, volume: str, image: str, domains: list[str]) -> None:
        """Atomically commit a versioned allowed-domain record."""
        payload = json.dumps(
            {"schema": _STATE_SCHEMA, "domains": domains}, separators=(",", ":")
        )
        script = (
            "umask 077; "
            f"tmp={_STATE_PATH}.tmp.$$; "
            f'cat > "$tmp" && mv -f "$tmp" {_STATE_PATH}'
        )
        result = self._runner.engine.run(
            "run",
            "--rm",
            "-i",
            "-v",
            f"{volume}:/data/auth",
            "--entrypoint",
            "sh",
            image,
            "-c",
            script,
            check=False,
            input=payload,
        )
        if result.returncode != 0:
            raise ProxyStateError(
                "Could not commit durable allowed-domain state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )

    def restore(
        self,
        volume: str,
        image: str,
        previous: list[str] | None,
    ) -> None:
        """Restore the record captured before a failed proxy transaction."""
        if previous is not None:
            self.write(volume, image, previous)
            return
        result = self._runner.engine.run(
            "run",
            "--rm",
            "-v",
            f"{volume}:/data/auth",
            "--entrypoint",
            "sh",
            image,
            "-c",
            f"rm -f {_STATE_PATH}",
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStateError(
                "Could not restore durable allowed-domain state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )
