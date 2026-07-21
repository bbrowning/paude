"""Exec command builders for container and pod execution."""

from __future__ import annotations

from collections.abc import Callable

ExecCmdBuilder = Callable[[str], list[str]]


def podman_exec_builder(container_name: str, engine: str = "podman") -> ExecCmdBuilder:
    """Return a callable that builds a podman/docker exec command for a bash command."""

    def build(bash_cmd: str) -> list[str]:
        return [engine, "exec", container_name, "bash", "-c", bash_cmd]

    return build
