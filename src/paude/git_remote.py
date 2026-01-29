"""Git remote URL construction for paude sessions.

This module provides utilities for setting up git remotes that communicate
with paude containers using the ext:: protocol.
"""

from __future__ import annotations

import subprocess
import sys


def build_openshift_remote_url(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    workspace_path: str = "/pvc/workspace",
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
    workspace_path: str = "/pvc/workspace",
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
    init_cmd = (
        f"test -d /pvc/workspace/.git || git init -b {branch} /pvc/workspace && "
        "git -C /pvc/workspace config receive.denyCurrentBranch updateInstead"
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
    init_cmd = (
        f"test -d /pvc/workspace/.git || git init -b {branch} /pvc/workspace && "
        "git -C /pvc/workspace config receive.denyCurrentBranch updateInstead"
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
    oc_cmd.extend([
        "get", "pod", pod_name, "-n", namespace,
        "-o", "jsonpath={.status.phase}"
    ])

    result = subprocess.run(
        oc_cmd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip().lower() == "running"
    return False


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
