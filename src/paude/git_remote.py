"""Git remote URL construction for paude sessions.

This module provides utilities for setting up git remotes that communicate
with paude containers using the ext:: protocol.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from paude.constants import CONTAINER_HOME, CONTAINER_WORKSPACE


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


def git_remote_remove(remote_name: str) -> bool:
    """Remove a git remote.

    Args:
        remote_name: Name of the remote to remove.

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        ["git", "remote", "remove", remote_name],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        if "No such remote" in result.stderr:
            print(f"Remote '{remote_name}' does not exist.", file=sys.stderr)
        else:
            print(f"Failed to remove remote: {result.stderr.strip()}", file=sys.stderr)
        return False

    return True


def list_paude_remotes() -> list[tuple[str, str]]:
    """List all paude git remotes.

    Returns:
        List of (remote_name, remote_url) tuples for remotes starting with "paude-".
    """
    result = subprocess.run(
        ["git", "remote", "-v"],
        capture_output=True,
        text=True,
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


def is_git_repository() -> bool:
    """Check if current directory is a git repository.

    Returns:
        True if in a git repository, False otherwise.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
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
    """Initialize git repository in a Podman container's workspace.

    Args:
        container_name: Name of the container.
        branch: Branch name to use for initial branch (matches local).

    Returns:
        True if successful, False if failed.
    """
    quoted_branch = shlex.quote(branch)
    init_cmd = (
        f"test -d {CONTAINER_WORKSPACE}/.git || "
        f"git init -b {quoted_branch} {CONTAINER_WORKSPACE} && "
        f"git -C {CONTAINER_WORKSPACE} config receive.denyCurrentBranch updateInstead"
    )
    result = subprocess.run(
        ["podman", "exec", container_name, "bash", "-c", init_cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to init workspace: {result.stderr}", file=sys.stderr)
        return False
    return True


def initialize_container_workspace_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    branch: str = "main",
) -> bool:
    """Initialize git repository in an OpenShift pod's workspace.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        context: Optional kubeconfig context.
        branch: Branch name to use for initial branch (matches local).

    Returns:
        True if successful, False if failed.
    """
    quoted_branch = shlex.quote(branch)
    init_cmd = (
        f"test -d {CONTAINER_WORKSPACE}/.git || "
        f"git init -b {quoted_branch} {CONTAINER_WORKSPACE} && "
        f"git -C {CONTAINER_WORKSPACE} config receive.denyCurrentBranch updateInstead"
    )
    oc_cmd = ["oc"]
    if context:
        oc_cmd.extend(["--context", context])
    oc_cmd.extend(["exec", pod_name, "-n", namespace, "--", "bash", "-c", init_cmd])

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to init workspace: {result.stderr}", file=sys.stderr)
        return False
    return True


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


def get_local_origin_url() -> str | None:
    """Get the URL of the local 'origin' remote.

    Returns:
        Origin URL or None if not set.
    """
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
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


def set_origin_in_container_podman(container_name: str, origin_url: str) -> bool:
    """Set the origin remote URL in a Podman container's workspace.

    Idempotent: adds origin if missing, updates URL if it exists.

    Args:
        container_name: Name of the container.
        origin_url: URL to set for the origin remote.

    Returns:
        True if successful, False if failed.
    """
    quoted_url = shlex.quote(origin_url)
    cmd = (
        f"git -C {CONTAINER_WORKSPACE} remote add origin {quoted_url} 2>/dev/null || "
        f"git -C {CONTAINER_WORKSPACE} remote set-url origin {quoted_url}"
    )
    result = subprocess.run(
        ["podman", "exec", container_name, "bash", "-c", cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to set origin in container: {result.stderr}", file=sys.stderr)
        return False
    return True


def set_origin_in_container_openshift(
    pod_name: str,
    namespace: str,
    origin_url: str,
    context: str | None = None,
) -> bool:
    """Set the origin remote URL in an OpenShift pod's workspace.

    Idempotent: adds origin if missing, updates URL if it exists.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        origin_url: URL to set for the origin remote.
        context: Optional kubeconfig context.

    Returns:
        True if successful, False if failed.
    """
    quoted_url = shlex.quote(origin_url)
    cmd = (
        f"git -C {CONTAINER_WORKSPACE} remote add origin {quoted_url} 2>/dev/null || "
        f"git -C {CONTAINER_WORKSPACE} remote set-url origin {quoted_url}"
    )
    oc_cmd = ["oc"]
    if context:
        oc_cmd.extend(["--context", context])
    oc_cmd.extend(["exec", pod_name, "-n", namespace, "--", "bash", "-c", cmd])

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to set origin in container: {result.stderr}", file=sys.stderr)
        return False
    return True


def fetch_tags_in_container_podman(container_name: str) -> bool:
    """Fetch tags from origin in a Podman container's workspace.

    Args:
        container_name: Name of the container.

    Returns:
        True if successful, False if failed.
    """
    result = subprocess.run(
        [
            "podman",
            "exec",
            container_name,
            "bash",
            "-c",
            f"git -C {CONTAINER_WORKSPACE} fetch origin --tags",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def fetch_tags_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Fetch tags from origin in an OpenShift pod's workspace.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        context: Optional kubeconfig context.

    Returns:
        True if successful, False if failed.
    """
    oc_cmd = ["oc"]
    if context:
        oc_cmd.extend(["--context", context])
    oc_cmd.extend(
        [
            "exec",
            pod_name,
            "-n",
            namespace,
            "--",
            "bash",
            "-c",
            f"git -C {CONTAINER_WORKSPACE} fetch origin --tags",
        ]
    )

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def setup_precommit_in_container_podman(container_name: str) -> bool:
    """Install pre-commit hooks in a Podman container's workspace.

    Only runs if .pre-commit-config.yaml exists in the workspace.

    Args:
        container_name: Name of the container.

    Returns:
        True if successful, False if failed.
    """
    cmd = (
        f"test -f {CONTAINER_WORKSPACE}/.pre-commit-config.yaml && "
        f"cd {CONTAINER_WORKSPACE} && pre-commit install"
    )
    result = subprocess.run(
        ["podman", "exec", container_name, "bash", "-c", cmd],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def setup_precommit_in_container_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Install pre-commit hooks in an OpenShift pod's workspace.

    Only runs if .pre-commit-config.yaml exists in the workspace.

    Args:
        pod_name: Name of the pod.
        namespace: Kubernetes namespace.
        context: Optional kubeconfig context.

    Returns:
        True if successful, False if failed.
    """
    cmd = (
        f'[[ -z "$HOME" || "$HOME" == "/" ]] && export HOME={CONTAINER_HOME}; '
        f"test -f {CONTAINER_WORKSPACE}/.pre-commit-config.yaml && "
        f"cd {CONTAINER_WORKSPACE} && pre-commit install"
    )
    oc_cmd = ["oc"]
    if context:
        oc_cmd.extend(["--context", context])
    oc_cmd.extend(["exec", pod_name, "-n", namespace, "--", "bash", "-c", cmd])

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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


def git_push_to_remote(remote_name: str, branch: str | None = None) -> bool:
    """Push to a git remote.

    Args:
        remote_name: Name of the remote to push to.
        branch: Branch to push (uses current branch if None).

    Returns:
        True if successful, False if failed.
    """
    branch = branch or get_current_branch() or "main"
    result = subprocess.run(
        ["git", "push", remote_name, branch],
        capture_output=False,  # Show output to user
    )
    return result.returncode == 0
