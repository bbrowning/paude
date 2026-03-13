"""Git remote URL construction for paude sessions.

This module provides utilities for setting up git remotes that communicate
with paude containers using the ext:: protocol.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from paude.constants import (
    BASE_REF_NAME,
    CLONE_FROM_ORIGIN_TIMEOUT,
    CONTAINER_HOME,
    CONTAINER_WORKSPACE,
)


def build_openshift_remote_url(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    workspace_path: str = CONTAINER_WORKSPACE,
) -> str:
    """Build a git ext:: remote URL for an OpenShift pod.

    The ext:: protocol tunnels git operations over stdin/stdout of an
    arbitrary command. This uses `oc exec` to run git inside the pod.

    Args:
        pod_name: Name of the pod (e.g., "paude-my-session-0").
        namespace: Kubernetes namespace.
        context: Optional kubeconfig context.
        workspace_path: Path to workspace inside the pod.

    Returns:
        Git remote URL in ext:: format.
    """
    # -i keeps stdin open for git protocol communication
    if context:
        cmd = f"oc --context {context} exec -i {pod_name} -n {namespace}"
    else:
        cmd = f"oc exec -i {pod_name} -n {namespace}"

    # %S expands to git-upload-pack/git-receive-pack (the executable name)
    return f"ext::{cmd} -- %S {workspace_path}"


def build_podman_remote_url(
    container_name: str,
    workspace_path: str = CONTAINER_WORKSPACE,
) -> str:
    """Build a git ext:: remote URL for a Podman container.

    Args:
        container_name: Name of the container (e.g., "paude-my-session").
        workspace_path: Path to workspace inside the container.

    Returns:
        Git remote URL in ext:: format.
    """
    # -i keeps stdin open for git protocol communication
    # %S expands to git-upload-pack/git-receive-pack (the executable name)
    return f"ext::podman exec -i {container_name} %S {workspace_path}"


def is_ext_protocol_allowed() -> bool:
    """Check if git ext:: protocol is allowed.

    Git disables the ext:: transport by default for security.
    Users must explicitly enable it.

    Returns:
        True if ext protocol is allowed, False otherwise.
    """
    result = subprocess.run(
        ["git", "config", "--get", "protocol.ext.allow"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        value = result.stdout.strip().lower()
        return value in ("always", "user")
    return False


def enable_ext_protocol() -> bool:
    """Enable git ext:: protocol for the current repository.

    Returns:
        True if successful, False otherwise.
    """
    result = subprocess.run(
        ["git", "config", "protocol.ext.allow", "always"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_remote_add(remote_name: str, remote_url: str) -> bool:
    """Add a git remote.

    Args:
        remote_name: Name for the remote (e.g., "paude-my-session").
        remote_url: Remote URL (ext:: format).

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        ["git", "remote", "add", remote_name, remote_url],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if "already exists" in result.stderr:
            print(
                f"Remote '{remote_name}' already exists. "
                f"Use 'git remote set-url' to update it.",
                file=sys.stderr,
            )
        else:
            print(f"Failed to add remote: {result.stderr.strip()}", file=sys.stderr)
        return False

    return True


def git_remote_remove(remote_name: str, cwd: Path | None = None) -> bool:
    """Remove a git remote.

    Args:
        remote_name: Name of the remote to remove.
        cwd: Working directory for the command.

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        ["git", "remote", "remove", remote_name],
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode != 0:
        if "No such remote" in result.stderr:
            print(f"Remote '{remote_name}' does not exist.", file=sys.stderr)
        else:
            print(f"Failed to remove remote: {result.stderr.strip()}", file=sys.stderr)
        return False

    return True


def list_paude_remotes(cwd: Path | None = None) -> list[tuple[str, str]]:
    """List all paude git remotes.

    Args:
        cwd: Working directory for the command.

    Returns:
        List of (remote_name, remote_url) tuples for remotes starting with "paude-".
    """
    result = subprocess.run(
        ["git", "remote", "-v"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )

    if result.returncode != 0:
        return []

    remotes: list[tuple[str, str]] = []
    seen: set[str] = set()

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Format: "name\turl (fetch|push)"
        # Split on tab first to get name and rest
        parts = line.split("\t", 1)
        if len(parts) >= 2:
            name = parts[0]
            # URL is everything up to the last space (which is "(fetch)" or "(push)")
            url_part = parts[1].rsplit(" ", 1)[0] if " " in parts[1] else parts[1]
            if name.startswith("paude-") and name not in seen:
                remotes.append((name, url_part))
                seen.add(name)

    return remotes


def is_git_repository(cwd: Path | None = None) -> bool:
    """Check if a directory is a git repository.

    Args:
        cwd: Directory to check. Defaults to current directory.

    Returns:
        True if in a git repository, False otherwise.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode == 0


def get_current_branch() -> str | None:
    """Get the current git branch name.

    Returns:
        Branch name or None if not on a branch.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def initialize_container_workspace_podman(
    container_name: str,
    branch: str = "main",
) -> bool:
    """Initialize git repository in a Podman container's workspace."""
    bash_cmd = _build_workspace_init_cmd(branch)
    exec_cmd = _build_podman_exec_cmd(container_name, bash_cmd)
    return _exec_in_container(exec_cmd, error_msg="Failed to init workspace")


def initialize_container_workspace_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    branch: str = "main",
) -> bool:
    """Initialize git repository in an OpenShift pod's workspace."""
    bash_cmd = _build_workspace_init_cmd(branch)
    exec_cmd = _build_openshift_exec_cmd(pod_name, namespace, context, bash_cmd)
    return _exec_in_container(exec_cmd, error_msg="Failed to init workspace")


def is_container_running_podman(container_name: str) -> bool:
    """Check if a Podman container is running.

    Args:
        container_name: Name of the container.

    Returns:
        True if running, False otherwise.
    """
    result = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Running}}", container_name],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().lower() == "true"
    return False


def is_pod_running_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Check if an OpenShift pod is running.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        context: Optional kubeconfig context.

    Returns:
        True if running, False otherwise.
    """
    oc_cmd = ["oc"]
    if context:
        oc_cmd.extend(["--context", context])
    oc_cmd.extend(
        ["get", "pod", pod_name, "-n", namespace, "-o", "jsonpath={.status.phase}"]
    )

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().lower() == "running"
    return False


def _build_podman_exec_cmd(container_name: str, bash_cmd: str) -> list[str]:
    """Build a podman exec command to run a bash command in a container."""
    return ["podman", "exec", container_name, "bash", "-c", bash_cmd]


def _build_openshift_exec_cmd(
    pod_name: str, namespace: str, context: str | None, bash_cmd: str
) -> list[str]:
    """Build an oc exec command to run a bash command in a pod."""
    cmd = ["oc"]
    if context:
        cmd.extend(["--context", context])
    cmd.extend(["exec", pod_name, "-n", namespace, "--", "bash", "-c", bash_cmd])
    return cmd


def _exec_in_container(
    exec_cmd: list[str],
    error_msg: str | None = None,
    timeout: int | None = None,
) -> bool:
    """Run a command in a container and return success status."""
    result = subprocess.run(exec_cmd, capture_output=True, text=True, timeout=timeout)
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


def ssh_url_to_https(url: str) -> str:
    """Convert a git SSH URL to HTTPS format.

    Converts URLs like:
        git@github.com:user/repo.git -> https://github.com/user/repo.git
        ssh://git@github.com/user/repo.git -> https://github.com/user/repo.git

    Non-SSH URLs are returned unchanged.

    Args:
        url: Git remote URL (SSH or HTTPS).

    Returns:
        HTTPS URL if input was SSH, otherwise the original URL.
    """
    import re

    # git@host:user/repo.git format
    match = re.match(r"^[\w.-]+@([\w.-]+):(.*)", url)
    if match:
        host = match.group(1)
        path = match.group(2)
        return f"https://{host}/{path}"

    # ssh://git@host/user/repo.git format
    match = re.match(r"^ssh://[\w.-]+@([\w.-]+)/(.*)", url)
    if match:
        host = match.group(1)
        path = match.group(2)
        return f"https://{host}/{path}"

    return url


def get_branch_remote_url(
    branch: str | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    """Get the remote URL for the current branch's tracking remote.

    Reads ``branch.<branch>.remote`` from git config to find the tracking
    remote, then returns that remote's URL. Falls back to "origin" if
    no tracking remote is configured.

    Args:
        branch: Branch name. Defaults to current branch or "main".
        cwd: Working directory for git commands. Defaults to current directory.
    """
    branch = branch or get_current_branch() or "main"

    # Get tracking remote name for branch
    result = subprocess.run(
        ["git", "config", "--get", f"branch.{branch}.remote"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    remote_name = result.stdout.strip() if result.returncode == 0 else "origin"

    # Get URL for that remote
    result = subprocess.run(
        ["git", "config", "--get", f"remote.{remote_name}.url"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def git_push_tags_to_remote(remote_name: str) -> bool:
    """Push all tags to a git remote.

    Args:
        remote_name: Name of the remote to push tags to.

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        ["git", "push", remote_name, "--tags"],
        capture_output=False,
    )
    return result.returncode == 0


def resolve_origin_cmd(
    branch: str | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    """Resolve the branch's tracking remote URL and build a set-origin command.

    Combines get_branch_remote_url, ssh_url_to_https, and the internal
    set-origin command builder into a single public helper.

    Returns:
        A bash command string to set origin in the container, or None
        if no remote URL could be resolved.
    """
    origin_url = get_branch_remote_url(branch, cwd=cwd)
    if not origin_url:
        return None
    return _build_set_origin_cmd(ssh_url_to_https(origin_url))


def set_origin_in_container_podman(container_name: str, origin_url: str) -> bool:
    """Set the origin remote URL in a Podman container's workspace."""
    bash_cmd = _build_set_origin_cmd(origin_url)
    exec_cmd = _build_podman_exec_cmd(container_name, bash_cmd)
    return _exec_in_container(exec_cmd, error_msg="Failed to set origin in container")


def set_origin_in_container_openshift(
    pod_name: str,
    namespace: str,
    origin_url: str,
    context: str | None = None,
) -> bool:
    """Set the origin remote URL in an OpenShift pod's workspace."""
    bash_cmd = _build_set_origin_cmd(origin_url)
    exec_cmd = _build_openshift_exec_cmd(pod_name, namespace, context, bash_cmd)
    return _exec_in_container(exec_cmd, error_msg="Failed to set origin in container")


_SET_BASE_REF_CMD = f"git -C {CONTAINER_WORKSPACE} update-ref {BASE_REF_NAME} HEAD"


def set_base_ref_in_container_podman(container_name: str) -> bool:
    """Set refs/paude/base to HEAD in a Podman container's workspace."""
    exec_cmd = _build_podman_exec_cmd(container_name, _SET_BASE_REF_CMD)
    return _exec_in_container(exec_cmd, error_msg="Failed to set base ref")


def set_base_ref_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Set refs/paude/base to HEAD in an OpenShift pod's workspace."""
    exec_cmd = _build_openshift_exec_cmd(
        pod_name, namespace, context, _SET_BASE_REF_CMD
    )
    return _exec_in_container(exec_cmd, error_msg="Failed to set base ref")


def setup_precommit_in_container_podman(container_name: str) -> bool:
    """Install pre-commit hooks in a Podman container's workspace."""
    exec_cmd = _build_podman_exec_cmd(container_name, _PRECOMMIT_CMD)
    return _exec_in_container(exec_cmd)


def setup_precommit_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Install pre-commit hooks in an OpenShift pod's workspace."""
    exec_cmd = _build_openshift_exec_cmd(
        pod_name, namespace, context, _PRECOMMIT_CMD_OPENSHIFT
    )
    return _exec_in_container(exec_cmd)


def git_fetch_from_remote(remote_name: str, cwd: Path | None = None) -> bool:
    """Fetch from a git remote.

    Args:
        remote_name: Name of the remote to fetch from.
        cwd: Working directory for the command.

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        ["git", "fetch", remote_name],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        print(
            f"Failed to fetch from '{remote_name}': {result.stderr.strip()}",
            file=sys.stderr,
        )
        return False
    return True


def git_diff_stat(ref_a: str, ref_b: str, cwd: Path | None = None) -> str:
    """Get diff stat between two git refs.

    Args:
        ref_a: First ref (e.g., "main").
        ref_b: Second ref (e.g., branch name).
        cwd: Working directory for the command.

    Returns:
        Diff stat output string, or empty string on failure.
    """
    result = subprocess.run(
        ["git", "diff", "--stat", f"{ref_a}...{ref_b}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _build_clone_from_origin_cmd(origin_https_url: str) -> str:
    """Build bash command to clone a repo from origin inside a container.

    The clone is unauthenticated. Private repos will fail and the caller
    should fall back to a full push.
    """
    quoted_url = shlex.quote(origin_https_url)
    return (
        f"git clone {quoted_url} {CONTAINER_WORKSPACE} && "
        f"git -C {CONTAINER_WORKSPACE} config receive.denyCurrentBranch updateInstead"
    )


def clone_from_origin_podman(
    container_name: str,
    origin_https_url: str,
) -> bool:
    """Clone a repo from origin inside a Podman container.

    Returns True if clone succeeded, False otherwise.
    """
    bash_cmd = _build_clone_from_origin_cmd(origin_https_url)
    exec_cmd = _build_podman_exec_cmd(container_name, bash_cmd)
    try:
        return _exec_in_container(exec_cmd, timeout=CLONE_FROM_ORIGIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("Clone from origin timed out.", file=sys.stderr)
        return False


def clone_from_origin_openshift(
    pod_name: str,
    namespace: str,
    origin_https_url: str,
    context: str | None = None,
) -> bool:
    """Clone a repo from origin inside an OpenShift pod.

    Returns True if clone succeeded, False otherwise.
    """
    bash_cmd = _build_clone_from_origin_cmd(origin_https_url)
    exec_cmd = _build_openshift_exec_cmd(pod_name, namespace, context, bash_cmd)
    try:
        return _exec_in_container(exec_cmd, timeout=CLONE_FROM_ORIGIN_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("Clone from origin timed out.", file=sys.stderr)
        return False


def git_push_to_remote(
    remote_name: str, branch: str | None = None, *, quiet: bool = False
) -> bool:
    """Push to a git remote.

    Args:
        remote_name: Name of the remote to push to.
        branch: Branch to push (uses current branch if None).
        quiet: If True, capture output instead of showing it.

    Returns:
        True if successful, False if failed.
    """
    branch = branch or get_current_branch() or "main"
    result = subprocess.run(
        ["git", "push", remote_name, branch],
        capture_output=quiet,
    )
    return result.returncode == 0


def count_local_only_commits(branch: str) -> int | None:
    """Count commits in HEAD that are not in origin/<branch>.

    Returns:
        Number of local-only commits, or None if comparison is not possible
        (e.g., no origin remote, tracking ref not fetched).
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{branch}..HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None
