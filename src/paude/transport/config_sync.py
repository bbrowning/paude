"""Remote config sync — transfers local files to a remote host for mounting."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from paude.platform import is_macos

if TYPE_CHECKING:
    from paude.transport.base import Transport
    from paude.transport.ssh import SshTransport


@dataclass
class RemoteConfigPaths:
    """Tracks remote paths created for config file syncing."""

    remote_base: str
    path_map: dict[str, str] = field(default_factory=dict)


def _parse_mount_sources(mounts: list[str]) -> list[str]:
    """Extract local source paths from -v mount arguments.

    Parses args like ["-v", "/local/path:/container/path:ro", ...] and
    returns the list of local source paths.
    """
    sources: list[str] = []
    i = 0
    while i < len(mounts):
        if mounts[i] == "-v" and i + 1 < len(mounts):
            mount_spec = mounts[i + 1]
            # Format: source:dest[:options]
            parts = mount_spec.split(":")
            if len(parts) >= 2:
                source = parts[0]
                # Skip named volumes (no leading /)
                if source.startswith("/"):
                    sources.append(source)
            i += 2
        else:
            i += 1
    return sources


def _transfer_path(transport: SshTransport, local_path: str, remote_path: str) -> bool:
    """Transfer a local file or directory to remote via tar pipe.

    Returns True on success, False on failure.
    """
    local = Path(local_path)
    if not local.exists():
        return False

    # Create remote parent directory
    remote_parent = str(Path(remote_path).parent)
    transport.run(["mkdir", "-p", remote_parent], check=False)

    if local.is_file():
        # Single file: cat | ssh cat > remote_path
        with open(local, "rb") as f:
            content = f.read()
        result = subprocess.run(
            [*transport.ssh_base(), "--", "cat", ">", remote_path],
            input=content,
            capture_output=True,
        )
        return result.returncode == 0
    elif local.is_dir():
        # Directory: tar | ssh tar
        tar_cmd = ["tar"]
        if is_macos():
            tar_cmd.append("--no-mac-metadata")
        tar_cmd.extend(["-cf", "-", "-C", str(local.parent), local.name])
        untar_cmd = [
            *transport.ssh_base(),
            "--",
            "tar",
            "--warning=no-unknown-keyword",
            "-xf",
            "-",
            "-C",
            remote_parent,
        ]
        tar_proc = subprocess.Popen(tar_cmd, stdout=subprocess.PIPE)
        try:
            untar_proc = subprocess.Popen(untar_cmd, stdin=tar_proc.stdout)
            if tar_proc.stdout:
                tar_proc.stdout.close()
            untar_proc.wait()
        finally:
            tar_proc.wait()
        return untar_proc.returncode == 0
    return False


def sync_configs_to_remote(
    transport: SshTransport,
    mounts: list[str],
    adc_path: Path | None = None,
) -> RemoteConfigPaths:
    """Transfer local files referenced in mounts to remote host.

    Creates a temp directory on the remote host and copies all local
    source paths from -v mount arguments into it, preserving the
    directory structure.

    Args:
        transport: SSH transport for remote execution.
        mounts: List of mount arguments (e.g. ["-v", "/src:/dst:ro", ...]).
        adc_path: Optional ADC credentials path to also transfer.

    Returns:
        RemoteConfigPaths with the remote base dir and path mapping.
    """
    # Create temp dir on remote
    result = transport.run(
        ["mktemp", "-d", "/tmp/paude-config-XXXX"],  # noqa: S108
        check=True,
    )
    remote_base = result.stdout.strip()

    paths = RemoteConfigPaths(remote_base=remote_base)

    # Parse mount sources
    sources = _parse_mount_sources(mounts)
    if adc_path and adc_path.is_file():
        sources.append(str(adc_path))

    # Transfer each source
    for i, local_path in enumerate(sources):
        local = Path(local_path)
        if not local.exists():
            continue

        remote_subdir = f"{remote_base}/{i}"
        remote_path = f"{remote_subdir}/{local.name}"
        if _transfer_path(transport, local_path, remote_path):
            paths.path_map[local_path] = remote_path

    return paths


def remap_mounts(mounts: list[str], path_map: dict[str, str]) -> list[str]:
    """Replace local source paths in -v args with remote paths.

    Args:
        mounts: Original mount arguments.
        path_map: Mapping of local_path -> remote_path.

    Returns:
        New mount arguments with sources replaced.
    """
    if not path_map:
        return list(mounts)

    result: list[str] = []
    i = 0
    while i < len(mounts):
        if mounts[i] == "-v" and i + 1 < len(mounts):
            mount_spec = mounts[i + 1]
            parts = mount_spec.split(":")
            if len(parts) >= 2 and parts[0] in path_map:
                parts[0] = path_map[parts[0]]
                result.append("-v")
                result.append(":".join(parts))
            else:
                result.append(mounts[i])
                result.append(mounts[i + 1])
            i += 2
        else:
            result.append(mounts[i])
            i += 1
    return result


def cleanup_remote_configs(transport: Transport, remote_base: str) -> None:
    """Remove remote temp directory."""
    if not remote_base or not remote_base.startswith("/tmp/paude-config-"):  # noqa: S108
        return
    transport.run(["rm", "-rf", remote_base], check=False)
