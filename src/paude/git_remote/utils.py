"""Pure git utility functions that don't exec into containers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paude.transport.base import Transport

from paude.constants import CONTAINER_WORKSPACE, DEFAULT_BRANCHES
from paude.git_remote.container_ops import _build_set_origin_cmd, _run_cmd


def build_openshift_remote_url(
    pod_name: str,
    namespace: str,
    context: str | None = None,
    workspace_path: str = CONTAINER_WORKSPACE,
) -> str:
    """Build a git ext:: remote URL for an OpenShift pod."""
    if context:
        cmd = f"oc --context {context} exec -i {pod_name} -n {namespace}"
    else:
        cmd = f"oc exec -i {pod_name} -n {namespace}"
    return f"ext::{cmd} -- %S {workspace_path}"


def build_podman_remote_url(
    container_name: str,
    workspace_path: str = CONTAINER_WORKSPACE,
    engine: str = "podman",
) -> str:
    """Build a git ext:: remote URL for a local container."""
    return f"ext::{engine} exec -i {container_name} %S {workspace_path}"


def build_ssh_remote_url(
    container_name: str,
    ssh_host: str,
    engine: str = "docker",
    ssh_key: str | None = None,
    ssh_port: int | None = None,
    workspace_path: str = CONTAINER_WORKSPACE,
) -> str:
    """Build a git ext:: remote URL tunneling through SSH to a remote container."""
    ssh_parts = ["ssh"]
    if ssh_key:
        ssh_parts.extend(["-i", ssh_key])
    if ssh_port:
        ssh_parts.extend(["-p", str(ssh_port)])
    ssh_parts.append(ssh_host)

    ssh_cmd = " ".join(ssh_parts)
    return f"ext::{ssh_cmd} {engine} exec -i {container_name} %S {workspace_path}"


def is_ext_protocol_allowed() -> bool:
    """Check if git ext:: protocol is allowed."""
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
    """Enable git ext:: protocol for the current repository."""
    result = subprocess.run(
        ["git", "config", "protocol.ext.allow", "always"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_remote_add(remote_name: str, remote_url: str) -> bool:
    """Add a git remote."""
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
    """Remove a git remote."""
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
    """List all paude git remotes."""
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
        parts = line.split("\t", 1)
        if len(parts) >= 2:
            name = parts[0]
            url_part = parts[1].rsplit(" ", 1)[0] if " " in parts[1] else parts[1]
            if name.startswith("paude-") and name not in seen:
                remotes.append((name, url_part))
                seen.add(name)

    return remotes


def is_git_repository(cwd: Path | None = None) -> bool:
    """Check if a directory is a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode == 0


def get_current_branch() -> str | None:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def ssh_url_to_https(url: str) -> str:
    """Convert a git SSH URL to HTTPS format."""
    import re

    match = re.match(r"^[\w.-]+@([\w.-]+):(.*)", url)
    if match:
        host = match.group(1)
        path = match.group(2)
        return f"https://{host}/{path}"

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
    """Get the remote URL for the current branch's tracking remote."""
    branch = branch or get_current_branch() or "main"

    result = subprocess.run(
        ["git", "config", "--get", f"branch.{branch}.remote"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    remote_name = result.stdout.strip() if result.returncode == 0 else "origin"

    result = subprocess.run(
        ["git", "config", "--get", f"remote.{remote_name}.url"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def get_upstream_url(cwd: str | Path | None = None) -> str | None:
    """Get the canonical upstream remote URL for the repository."""
    for default_branch in DEFAULT_BRANCHES:
        url = get_branch_remote_url(default_branch, cwd=cwd)
        if url:
            return url

    return get_branch_remote_url(None, cwd=cwd)


def resolve_origin_cmd(
    cwd: str | Path | None = None,
) -> str | None:
    """Resolve the upstream remote URL and build a set-origin command."""
    origin_url = get_upstream_url(cwd=cwd)
    if not origin_url:
        return None
    return _build_set_origin_cmd(ssh_url_to_https(origin_url))


def git_push_tags_to_remote(remote_name: str) -> bool:
    """Push all tags to a git remote."""
    result = subprocess.run(
        ["git", "push", remote_name, "--tags"],
        capture_output=False,
    )
    return result.returncode == 0


def git_fetch_from_remote(remote_name: str, cwd: Path | None = None) -> bool:
    """Fetch from a git remote."""
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
    """Get diff stat between two git refs."""
    result = subprocess.run(
        ["git", "diff", "--stat", f"{ref_a}...{ref_b}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def git_push_to_remote(
    remote_name: str, branch: str | None = None, *, quiet: bool = False
) -> bool:
    """Push to a git remote."""
    branch = branch or get_current_branch() or "main"
    result = subprocess.run(
        ["git", "push", remote_name, branch],
        capture_output=quiet,
    )
    return result.returncode == 0


def count_local_only_commits(branch: str) -> int | None:
    """Count commits in HEAD that are not in origin/<branch>."""
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


def is_container_running_podman(
    container_name: str,
    engine: str = "podman",
    transport: Transport | None = None,
) -> bool:
    """Check if a local or remote container is running."""
    cmd = [engine, "inspect", "--format", "{{.State.Running}}", container_name]
    result = _run_cmd(cmd, transport=transport)
    if result.returncode == 0:
        return result.stdout.strip().lower() == "true"
    return False


def is_pod_running_openshift(
    pod_name: str,
    namespace: str,
    context: str | None = None,
) -> bool:
    """Check if an OpenShift pod is running."""
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
