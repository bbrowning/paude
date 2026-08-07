"""Shared utilities for port-forward PID file management and spec parsing."""

from __future__ import annotations

import ipaddress
import os
import signal
from functools import lru_cache
from pathlib import Path

# Default host interface to bind forwarded ports to. Loopback keeps a
# forwarded port private to the machine running the paude CLI unless the
# user explicitly opts into a different bind address via HOST_IP:HOST:CONTAINER.
DEFAULT_BIND_IP = "127.0.0.1"

# A single forwarded port, normalized as (host_ip, host_port, container_port).
ForwardPort = tuple[str, int, int]


def _parse_port(value: str, spec: str) -> int:
    """Parse a single TCP port, validating the 1-65535 range."""
    text = value.strip()
    try:
        port = int(text)
    except ValueError:
        raise ValueError(
            f"invalid port spec '{spec}': '{text}' is not an integer"
        ) from None
    if not 1 <= port <= 65535:
        raise ValueError(
            f"invalid port spec '{spec}': port {port} is out of range 1-65535"
        )
    return port


def _validate_host_ip(host_ip: str, spec: str) -> None:
    """Validate that host_ip is a literal IP address, not a hostname."""
    try:
        ipaddress.ip_address(host_ip)
    except ValueError:
        raise ValueError(
            f"invalid port spec '{spec}': '{host_ip}' is not a valid IP address"
        ) from None


def parse_forward_port_spec(spec: str) -> ForwardPort:
    """Parse a single ``--forward-port`` spec into a normalized tuple.

    Accepted forms:

    * ``PORT`` -> ``(127.0.0.1, PORT, PORT)`` (same port on both sides)
    * ``HOST:CONTAINER`` -> ``(127.0.0.1, HOST, CONTAINER)``
    * ``HOST_IP:HOST:CONTAINER`` -> ``(HOST_IP, HOST, CONTAINER)``

    Args:
        spec: The raw spec string.

    Returns:
        A ``(host_ip, host_port, container_port)`` tuple.

    Raises:
        ValueError: If the spec is empty, malformed, or has out-of-range ports.
    """
    raw = spec.strip()
    if not raw:
        raise ValueError("invalid port spec: empty value")

    parts = raw.split(":")
    if len(parts) == 1:
        host_ip = DEFAULT_BIND_IP
        host_raw = container_raw = parts[0]
    elif len(parts) == 2:  # noqa: PLR2004 - HOST:CONTAINER
        host_ip = DEFAULT_BIND_IP
        host_raw, container_raw = parts
    elif len(parts) == 3:  # noqa: PLR2004 - HOST_IP:HOST:CONTAINER
        host_ip, host_raw, container_raw = parts
        host_ip = host_ip.strip()
        if not host_ip:
            raise ValueError(f"invalid port spec '{spec}': empty host IP")
        _validate_host_ip(host_ip, spec)
    else:
        raise ValueError(
            f"invalid port spec '{spec}': expected PORT, HOST:CONTAINER, "
            "or HOST_IP:HOST:CONTAINER"
        )

    return (host_ip, _parse_port(host_raw, spec), _parse_port(container_raw, spec))


def parse_forward_port_specs(specs: list[str]) -> list[ForwardPort]:
    """Parse and de-duplicate a list of ``--forward-port`` specs.

    Exact duplicate binds are collapsed silently. Two specs that bind the same
    ``host_ip:host_port`` to different container ports are a conflict and raise.

    Args:
        specs: Raw spec strings.

    Returns:
        Normalized ``(host_ip, host_port, container_port)`` tuples, in order.

    Raises:
        ValueError: If a spec is malformed or two specs conflict on a host bind.
    """
    result: list[ForwardPort] = []
    seen: dict[tuple[str, int], int] = {}
    for spec in specs:
        host_ip, host_port, container_port = parse_forward_port_spec(spec)
        key = (host_ip, host_port)
        if key in seen:
            if seen[key] != container_port:
                raise ValueError(
                    f"conflicting port forwards for {host_ip}:{host_port} "
                    f"(-> {seen[key]} and -> {container_port})"
                )
            continue
        seen[key] = container_port
        result.append((host_ip, host_port, container_port))
    return result


def merge_forward_ports(
    user_ports: list[ForwardPort],
    agent_ports: list[tuple[int, int]],
) -> list[ForwardPort]:
    """Merge user opt-in forwards with agent-declared exposed ports.

    Agent ports are assumed to bind loopback. User forwards win on a
    ``(host_ip, host_port)`` conflict; agent ports fill in anything left over.
    """
    merged: list[ForwardPort] = []
    seen: set[tuple[str, int]] = set()

    for host_ip, host_port, container_port in user_ports:
        key = (host_ip, host_port)
        if key not in seen:
            seen.add(key)
            merged.append((host_ip, host_port, container_port))

    for host_port, container_port in agent_ports:
        key = (DEFAULT_BIND_IP, host_port)
        if key not in seen:
            seen.add(key)
            merged.append((DEFAULT_BIND_IP, host_port, container_port))

    return merged


@lru_cache(maxsize=1)
def pid_dir() -> Path:
    """Return the directory for storing port-forward PID files."""
    d = Path.home() / ".local" / "share" / "paude" / "port-forwards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_file(session_name: str) -> Path:
    """Return the PID file path for a session's port-forward."""
    return pid_dir() / f"{session_name}.pid"


def log_file(session_name: str) -> Path:
    """Return the log file path for a session's port-forward."""
    return pid_dir() / f"{session_name}.log"


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is still running.

    Detects zombie (defunct) children and reaps them, returning False.
    Also detects non-child zombies on Linux via /proc.
    """
    try:
        wait_pid, _ = os.waitpid(pid, os.WNOHANG)
        if wait_pid != 0:
            return False
    except ChildProcessError:
        pass
    except OSError:
        return False

    try:
        os.kill(pid, 0)
    except OSError:
        return False

    # Check for zombie state via /proc (catches non-child zombies on Linux)
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("State:"):
                    state = line.split()[1]
                    return state != "Z"
    except OSError:
        pass

    return True


def check_running_pid(session_name: str) -> bool:
    """Return True if a port-forward process is already running for this session.

    Cleans up stale PID files as a side effect.
    """
    pf = pid_file(session_name)
    if not pf.is_file():
        return False
    try:
        pid = int(pf.read_text().strip())
        if is_process_running(pid):
            return True
    except (ValueError, OSError):
        pass
    pf.unlink(missing_ok=True)
    return False


def stop_port_forward(session_name: str) -> None:
    """Stop a port-forward process by session name and clean up the PID file."""
    pf = pid_file(session_name)
    if not pf.is_file():
        return

    try:
        pid = int(pf.read_text().strip())
        if is_process_running(pid):
            os.kill(pid, signal.SIGTERM)
    except (ValueError, OSError):
        pass

    pf.unlink(missing_ok=True)
    log_file(session_name).unlink(missing_ok=True)
