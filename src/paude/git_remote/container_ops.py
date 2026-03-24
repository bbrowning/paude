"""Container operations: exec-based git commands inside containers/pods."""

from __future__ import annotations

import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.transport.base import Transport

from paude.constants import (
    CLONE_FROM_ORIGIN_TIMEOUT,
    CONTAINER_HOME,
    CONTAINER_WORKSPACE,
)
from paude.git_remote.exec_cmd import (
    ExecCmdBuilder,
    openshift_exec_builder,
    podman_exec_builder,
)


def _run_cmd(
    cmd: list[str],
    transport: Transport | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command locally or via transport."""
    if transport and transport.is_remote:
        return transport.run(cmd, check=False, timeout=timeout)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _exec_in_container(
    exec_cmd: list[str],
    error_msg: str | None = None,
    timeout: int | None = None,
    transport: Transport | None = None,
) -> bool:
    """Run a command in a container and return success status."""
    result = _run_cmd(exec_cmd, transport=transport, timeout=timeout)
    if result.returncode != 0 and error_msg:
        print(f"{error_msg}: {result.stderr}", file=sys.stderr)
    return result.returncode == 0


def _build_workspace_init_cmd(branch: str) -> str:
    """Build bash command to initialize a git workspace."""
    quoted_branch = shlex.quote(branch)
    return (
        f"test -d {CONTAINER_WORKSPACE}/.git || "
        f"git init -b {quoted_branch} {CONTAINER_WORKSPACE} && "
        f"git -C {CONTAINER_WORKSPACE} config receive.denyCurrentBranch updateInstead"
    )


def _build_set_origin_cmd(origin_url: str) -> str:
    """Build bash command to set the origin remote URL."""
    quoted_url = shlex.quote(origin_url)
    return (
        f"git -C {CONTAINER_WORKSPACE} remote add origin {quoted_url} 2>/dev/null || "
        f"git -C {CONTAINER_WORKSPACE} remote set-url origin {quoted_url}"
    )


_PRECOMMIT_CMD = (
    f"test -f {CONTAINER_WORKSPACE}/.pre-commit-config.yaml && "
    f"cd {CONTAINER_WORKSPACE} && pre-commit install"
)

_PRECOMMIT_CMD_OPENSHIFT = (
    f'[[ -z "$HOME" || "$HOME" == "/" ]] && export HOME={CONTAINER_HOME}; '
    f"{_PRECOMMIT_CMD}"
)

from paude.constants import BASE_REF_NAME  # noqa: E402

_SET_BASE_REF_CMD = f"git -C {CONTAINER_WORKSPACE} update-ref {BASE_REF_NAME} HEAD"


def _build_clone_from_origin_cmd(origin_https_url: str) -> str:
    """Build bash command to clone a repo from origin inside a container."""
    quoted_url = shlex.quote(origin_https_url)
    return (
        f"git clone {quoted_url} {CONTAINER_WORKSPACE} && "
        f"git -C {CONTAINER_WORKSPACE} config receive.denyCurrentBranch updateInstead"
    )


# --- Unified functions ---


def initialize_container_workspace(
    exec_builder: ExecCmdBuilder,
    branch: str = "main",
    transport: Transport | None = None,
) -> bool:
    """Initialize git repository in a container's workspace."""
    bash_cmd = _build_workspace_init_cmd(branch)
    exec_cmd = exec_builder(bash_cmd)
    return _exec_in_container(
        exec_cmd, error_msg="Failed to init workspace", transport=transport
    )


def set_origin_in_container(
    exec_builder: ExecCmdBuilder,
    origin_url: str,
    transport: Transport | None = None,
) -> bool:
    """Set the origin remote URL in a container's workspace."""
    bash_cmd = _build_set_origin_cmd(origin_url)
    exec_cmd = exec_builder(bash_cmd)
    return _exec_in_container(
        exec_cmd, error_msg="Failed to set origin in container", transport=transport
    )


def set_base_ref_in_container(
    exec_builder: ExecCmdBuilder,
    transport: Transport | None = None,
) -> bool:
    """Set refs/paude/base to HEAD in a container's workspace."""
    exec_cmd = exec_builder(_SET_BASE_REF_CMD)
    return _exec_in_container(
        exec_cmd, error_msg="Failed to set base ref", transport=transport
    )


def setup_precommit_in_container(
    exec_builder: ExecCmdBuilder,
    set_home: bool = False,
    transport: Transport | None = None,
) -> bool:
    """Install pre-commit hooks in a container's workspace."""
    cmd = _PRECOMMIT_CMD_OPENSHIFT if set_home else _PRECOMMIT_CMD
    exec_cmd = exec_builder(cmd)
    return _exec_in_container(exec_cmd, transport=transport)


def clone_from_origin(
    exec_builder: ExecCmdBuilder,
    origin_https_url: str,
    timeout: int | None = None,
    transport: Transport | None = None,
) -> bool:
    """Clone a repo from origin inside a container.

    Returns True if clone succeeded, False otherwise.
    """
    bash_cmd = _build_clone_from_origin_cmd(origin_https_url)
    exec_cmd = exec_builder(bash_cmd)
    try:
        return _exec_in_container(
            exec_cmd, timeout=timeout or CLONE_FROM_ORIGIN_TIMEOUT, transport=transport
        )
    except subprocess.TimeoutExpired:
        print("Clone from origin timed out.", file=sys.stderr)
        return False


# --- Backward-compatible paired wrappers ---


def _build_podman_exec_cmd(
    container_name: str,
    bash_cmd: str,
    engine: str = "podman",
) -> list[str]:
    """Build a container exec command to run a bash command in a container."""
    return podman_exec_builder(container_name, engine)(bash_cmd)


def _build_openshift_exec_cmd(
    pod_name: str, namespace: str, context: str | None, bash_cmd: str
) -> list[str]:
    """Build an oc exec command to run a bash command in a pod."""
    return openshift_exec_builder(pod_name, namespace, context)(bash_cmd)


def initialize_container_workspace_podman(
    container_name: str,
    branch: str = "main",
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Initialize git repository in a local or remote container's workspace."""
    return initialize_container_workspace(
        podman_exec_builder(container_name, engine), branch, transport
    )


def initialize_container_workspace_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    branch: str = "main",
) -> bool:
    """Initialize git repository in an OpenShift pod's workspace."""
    return initialize_container_workspace(
        openshift_exec_builder(pod_name, namespace, context), branch
    )


def set_origin_in_container_podman(
    container_name: str,
    origin_url: str,
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Set the origin remote URL in a local or remote container's workspace."""
    return set_origin_in_container(
        podman_exec_builder(container_name, engine), origin_url, transport
    )


def set_origin_in_container_openshift(
    pod_name: str,
    namespace: str,
    origin_url: str,
    context: str | None = None,
) -> bool:
    """Set the origin remote URL in an OpenShift pod's workspace."""
    return set_origin_in_container(
        openshift_exec_builder(pod_name, namespace, context), origin_url
    )


def set_base_ref_in_container_podman(
    container_name: str,
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Set refs/paude/base to HEAD in a local or remote container's workspace."""
    return set_base_ref_in_container(
        podman_exec_builder(container_name, engine), transport
    )


def set_base_ref_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Set refs/paude/base to HEAD in an OpenShift pod's workspace."""
    return set_base_ref_in_container(
        openshift_exec_builder(pod_name, namespace, context)
    )


def setup_precommit_in_container_podman(
    container_name: str,
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Install pre-commit hooks in a local or remote container's workspace."""
    return setup_precommit_in_container(
        podman_exec_builder(container_name, engine), transport=transport
    )


def setup_precommit_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Install pre-commit hooks in an OpenShift pod's workspace."""
    return setup_precommit_in_container(
        openshift_exec_builder(pod_name, namespace, context), set_home=True
    )


def clone_from_origin_podman(
    container_name: str,
    origin_https_url: str,
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Clone a repo from origin inside a local or remote container."""
    return clone_from_origin(
        podman_exec_builder(container_name, engine),
        origin_https_url,
        transport=transport,
    )


def clone_from_origin_openshift(
    pod_name: str,
    namespace: str,
    origin_https_url: str,
    context: str | None = None,
) -> bool:
    """Clone a repo from origin inside an OpenShift pod."""
    return clone_from_origin(
        openshift_exec_builder(pod_name, namespace, context), origin_https_url
    )
