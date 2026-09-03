"""Durable mutable proxy configuration stored in the session auth volume."""

from __future__ import annotations

import json
import shlex

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
        return self._decode(result.stdout)

    def read_pair(
        self,
        other: ProxyStateStore,
        volume: str,
        image: str,
    ) -> tuple[list[str] | None, list[str] | None]:
        """Read two adjacent policy records with one helper container."""
        stores = (self, other)
        script = "; ".join(store._framed_read_command() for store in stores)
        result = self._runner.engine.run(
            "run",
            "--rm",
            "-v",
            f"{volume}:/data/auth:ro",
            "--entrypoint",
            "sh",
            image,
            "-c",
            script,
            check=False,
        )
        if result.returncode != 0:
            raise ProxyStateError(
                "Could not read durable proxy policy state: "
                f"{result.stderr.strip() or 'container helper failed'}"
            )
        if not isinstance(result.stdout, str):
            raise ProxyStateError("Durable proxy policy state is corrupt.")
        frames = result.stdout.split("\0")
        if len(frames) != 5 or frames[-1]:
            raise ProxyStateError("Durable proxy policy state is corrupt.")
        return (
            self._decode_frame(frames[0], frames[1]),
            other._decode_frame(frames[2], frames[3]),
        )

    def _framed_read_command(self) -> str:
        """Build a shell fragment that frames presence and arbitrary JSON."""
        path = shlex.quote(self._path)
        return (
            f"if test -e {path}; then printf '1\\0'; cat {path} || exit $?; "
            "printf '\\0'; else printf '0\\0\\0'; fi"
        )

    def _decode_frame(self, marker: str, payload: str) -> list[str] | None:
        """Decode one framed record while preserving legacy absence."""
        if marker == "0" and not payload:
            return None
        if marker != "1":
            raise ProxyStateError("Durable proxy policy state is corrupt.")
        return self._decode(payload)

    def _decode(self, payload: str) -> list[str]:
        """Validate and decode this store's versioned JSON record."""
        try:
            record = json.loads(payload)
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
