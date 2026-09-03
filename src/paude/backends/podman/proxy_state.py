"""Durable mutable proxy configuration stored in the session auth volume."""

from __future__ import annotations

import json

from paude.container.runner import ContainerRunner

_DOMAIN_STATE_PATH = "/data/auth/allowed-domains.json"
_DOMAIN_STATE_SCHEMA = "allowed-domains.v1"
_MISSING_EXIT = 3


class ProxyStateError(RuntimeError):
    """Durable proxy state could not be read or written safely."""


class ProxyStateStore:
    """Read and atomically write non-secret proxy state through the engine."""

    def __init__(
        self,
        runner: ContainerRunner,
        *,
        path: str = _DOMAIN_STATE_PATH,
        schema: str = _DOMAIN_STATE_SCHEMA,
        field: str = "domains",
        description: str = "allowed-domain",
    ) -> None:
        self._runner = runner
        self._path = path
        self._schema = schema
        self._field = field
        self._description = description

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
            f"test -e {self._path} || exit {_MISSING_EXIT}; cat {self._path}",
            check=False,
        )
        if result.returncode == _MISSING_EXIT:
            return None
        if result.returncode != 0:
            raise ProxyStateError(
                f"Could not read durable {self._description} state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )
        try:
            record = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ProxyStateError(
                f"Durable {self._description} state is corrupt."
            ) from exc
        if (
            not isinstance(record, dict)
            or record.get("schema") != self._schema
            or not isinstance(record.get(self._field), list)
            or not all(isinstance(item, str) for item in record[self._field])
        ):
            raise ProxyStateError(f"Durable {self._description} state is corrupt.")
        return list(record[self._field])

    def write(self, volume: str, image: str, values: list[str]) -> None:
        """Atomically commit a versioned proxy policy record."""
        payload = json.dumps(
            {"schema": self._schema, self._field: values}, separators=(",", ":")
        )
        script = (
            "umask 077; "
            f"tmp={self._path}.tmp.$$; "
            f'cat > "$tmp" && mv -f "$tmp" {self._path}'
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
                f"Could not commit durable {self._description} state: "
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
            f"rm -f {self._path}",
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStateError(
                f"Could not restore durable {self._description} state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )
