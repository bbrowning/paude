"""TCP proxy for podman port-forwarding.

This script runs as a standalone background process. For each incoming TCP
connection it spawns a ``podman exec`` (or ``ssh … podman exec``) subprocess
and shuttles data bidirectionally between the socket and the subprocess's
stdin/stdout.  This makes the container see every connection as originating
from 127.0.0.1 (via socat inside the container).

Usage::

    python -m paude.backends.podman.port_forward_proxy \
        --forward 18789 'podman exec -i mycontainer socat STDIO TCP:127.0.0.1:18789'
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import socket
import subprocess
import threading
from typing import NoReturn

BUFFER_SIZE = 65536


def _pipe_socket_to_proc(sock: socket.socket, proc: subprocess.Popen[bytes]) -> None:
    """Read from *sock* and write to *proc.stdin* until EOF."""
    try:
        assert proc.stdin is not None  # noqa: S101
        fd = proc.stdin.fileno()
        while True:
            data = sock.recv(BUFFER_SIZE)
            if not data:
                break
            os.write(fd, data)
    except (OSError, BrokenPipeError):
        pass
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass


def _pipe_proc_to_socket(proc: subprocess.Popen[bytes], sock: socket.socket) -> None:
    """Read from *proc.stdout* and write to *sock* until EOF."""
    try:
        assert proc.stdout is not None  # noqa: S101
        fd = proc.stdout.fileno()
        while True:
            data = os.read(fd, BUFFER_SIZE)
            if not data:
                break
            sock.sendall(data)
    except (OSError, BrokenPipeError):
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle_connection(client: socket.socket, exec_cmd: list[str]) -> None:
    """Handle a single client connection by tunnelling through *exec_cmd*."""
    try:
        proc = subprocess.Popen(  # noqa: S603
            exec_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        t1 = threading.Thread(
            target=_pipe_socket_to_proc, args=(client, proc), daemon=True
        )
        t2 = threading.Thread(
            target=_pipe_proc_to_socket, args=(proc, client), daemon=True
        )
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        proc.wait()
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP proxy for podman port-forwarding")
    parser.add_argument(
        "--forward",
        nargs=2,
        action="append",
        required=True,
        metavar=("PORT", "CMD"),
        help=(
            "Port and exec command: --forward <listen_port> '<shell-quoted cmd>'. "
            "May be repeated for multiple ports."
        ),
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="PID of the parent process; proxy exits when this process dies.",
    )
    return parser.parse_args(argv)


def _run_server(listen_port: int, exec_cmd: list[str]) -> socket.socket:
    """Create and bind a server socket, returning it."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", listen_port))
    server.listen(5)
    return server


def _accept_loop(server: socket.socket, exec_cmd: list[str]) -> None:
    """Accept connections on *server* and handle each in a thread."""
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            break
        threading.Thread(
            target=_handle_connection,
            args=(client, exec_cmd),
            daemon=True,
        ).start()


def _watch_parent(parent_pid: int, shutdown_event: threading.Event) -> None:
    """Poll whether *parent_pid* is still alive; set *shutdown_event* when it dies."""
    while not shutdown_event.is_set():
        try:
            os.kill(parent_pid, 0)  # noqa: S603 – signal 0 just checks existence
        except OSError:
            # Parent is gone – trigger shutdown.
            shutdown_event.set()
            return
        shutdown_event.wait(timeout=2)


def main(argv: list[str] | None = None) -> NoReturn:
    args = _parse_args(argv)

    servers: list[socket.socket] = []
    shutdown_event = threading.Event()

    for forward_args in args.forward:
        listen_port = int(forward_args[0])
        exec_cmd = shlex.split(forward_args[1])
        server = _run_server(listen_port, exec_cmd)
        servers.append(server)

        threading.Thread(
            target=_accept_loop,
            args=(server, exec_cmd),
            daemon=True,
        ).start()

    def _shutdown(*_args: object) -> NoReturn:
        for s in servers:
            try:
                s.close()
            except OSError:
                pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if args.parent_pid is not None:
        threading.Thread(
            target=_watch_parent,
            args=(args.parent_pid, shutdown_event),
            daemon=True,
        ).start()

    # Block until shutdown is requested (parent death or signal).
    shutdown_event.wait()
    _shutdown()


if __name__ == "__main__":
    main()
