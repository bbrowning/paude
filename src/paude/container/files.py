"""Read and atomically replace files inside running containers."""

from __future__ import annotations

import subprocess

from paude.container.engine import ContainerEngine


class ContainerFileManager:
    """Container file operations used for persistent mutable configuration."""

    def __init__(self, engine: ContainerEngine) -> None:
        self._engine = engine

    def read_file(self, container: str, path: str) -> str | None:
        """Return a file's content, or None when the file does not exist."""
        exists = self._engine.run("exec", container, "test", "-e", path, check=False)
        if exists.returncode != 0:
            return None
        result = self._engine.run("exec", container, "cat", path, check=False)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                [self._engine.binary, "exec", container, "cat", path],
                result.stdout,
                result.stderr,
            )
        return result.stdout

    def replace_file(
        self,
        container: str,
        path: str,
        content: str,
        *,
        owner: str,
        mode: str = "600",
    ) -> None:
        """Atomically replace a file without exposing partial content."""
        script = r"""
set -e
target="$1"
owner="$2"
mode="$3"
parent=$(dirname "$target")
mkdir -p "$parent"
chown "$owner" "$parent"
chmod g+rwX "$parent"
temporary=$(mktemp "${target}.tmp.XXXXXX")
trap 'rm -f "$temporary"' EXIT
cat > "$temporary"
chown "$owner" "$temporary"
chmod "$mode" "$temporary"
mv -f "$temporary" "$target"
trap - EXIT
"""
        self._engine.run(
            "exec",
            "-i",
            "--user",
            "root",
            container,
            "sh",
            "-c",
            script,
            "paude-config-replace",
            path,
            owner,
            mode,
            input=content,
        )

    def remove_file(self, container: str, path: str) -> None:
        """Remove a specific file when present."""
        self._engine.run("exec", container, "rm", "-f", path, check=False)
