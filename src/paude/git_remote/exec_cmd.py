"""Exec command builders for container and pod execution."""

from __future__ import annotations

from collections.abc import Callable

ExecCmdBuilder = Callable[[str], list[str]]


def podman_exec_builder(container_name: str, engine: str = "podman") -> ExecCmdBuilder:
    """Return a callable that builds a podman/docker exec command for a bash command."""

    def build(bash_cmd: str) -> list[str]:
        return [engine, "exec", container_name, "bash", "-c", bash_cmd]

    return build


def openshift_exec_builder(
    pod_name: str, namespace: str, context: str | None = None
) -> ExecCmdBuilder:
    """Return a callable that builds an oc exec command for a bash command."""

    def build(bash_cmd: str) -> list[str]:
        cmd = ["oc"]
        if context:
            cmd.extend(["--context", context])
        cmd.extend(["exec", pod_name, "-n", namespace, "--", "bash", "-c", bash_cmd])
        return cmd

    return build
