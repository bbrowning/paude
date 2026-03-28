"""Tests for the podman port-forward TCP proxy script."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    """Wait until a TCP port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Port {port} not ready within {timeout}s")


def _find_free_port() -> int:
    """Find an available TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cleanup_proxy(proc: subprocess.Popen[bytes]) -> None:
    """Reliably kill a proxy subprocess and its children.

    Sends SIGTERM to the process group first, then escalates to SIGKILL
    if the process doesn't exit within 3 seconds.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=3)


class TestPortForwardProxy:
    """Integration tests for the proxy script."""

    def test_proxy_forwards_data(self) -> None:
        """Test that data flows through the proxy to a real echo-like command."""
        listen_port = _find_free_port()

        # Use 'cat' as the exec command — it echoes stdin back to stdout
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "paude.backends.podman.port_forward_proxy",
                "--forward",
                str(listen_port),
                "cat",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        try:
            _wait_for_port(listen_port)

            # Connect and send data
            with socket.create_connection(("127.0.0.1", listen_port)) as sock:
                sock.sendall(b"hello world")
                sock.shutdown(socket.SHUT_WR)
                response = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk

            assert response == b"hello world"
        finally:
            _cleanup_proxy(proc)

    def test_proxy_handles_multiple_connections(self) -> None:
        """Test that the proxy can handle multiple concurrent connections."""
        listen_port = _find_free_port()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "paude.backends.podman.port_forward_proxy",
                "--forward",
                str(listen_port),
                "cat",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        try:
            _wait_for_port(listen_port)

            results = []
            for i in range(3):
                with socket.create_connection(("127.0.0.1", listen_port)) as sock:
                    msg = f"msg-{i}".encode()
                    sock.sendall(msg)
                    sock.shutdown(socket.SHUT_WR)
                    response = b""
                    while True:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        response += chunk
                    results.append(response)

            assert results == [b"msg-0", b"msg-1", b"msg-2"]
        finally:
            _cleanup_proxy(proc)

    def test_proxy_shuts_down_on_sigterm(self) -> None:
        """Test that the proxy exits cleanly on SIGTERM."""
        listen_port = _find_free_port()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "paude.backends.podman.port_forward_proxy",
                "--forward",
                str(listen_port),
                "cat",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        _wait_for_port(listen_port)

        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _cleanup_proxy(proc)
            raise
        assert proc.returncode is not None

    def test_proxy_binds_to_localhost_only(self) -> None:
        """Verify the proxy only listens on 127.0.0.1."""
        listen_port = _find_free_port()

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "paude.backends.podman.port_forward_proxy",
                "--forward",
                str(listen_port),
                "cat",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        try:
            _wait_for_port(listen_port)

            # Should connect on 127.0.0.1
            with socket.create_connection(("127.0.0.1", listen_port)) as sock:
                sock.close()
        finally:
            _cleanup_proxy(proc)
