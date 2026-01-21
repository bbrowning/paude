"""Container execution for paude."""

from __future__ import annotations

import subprocess
import sys
import time


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


class ContainerRunner:
    """Runs paude containers."""

    _proxy_counter = 0

    def run_claude(
        self,
        image: str,
        mounts: list[str],
        env: dict[str, str],
        args: list[str],
        workdir: str | None = None,
        network: str | None = None,
        yolo: bool = False,
        allow_network: bool = False,
    ) -> int:
        """Run the Claude container.

        Args:
            image: Container image to run.
            mounts: Volume mount arguments.
            env: Environment variables.
            args: Arguments to pass to claude.
            workdir: Working directory inside the container.
            network: Optional network to attach to.
            yolo: Enable YOLO mode (skip permission prompts).
            allow_network: Allow unrestricted network access.

        Returns:
            Exit code from the container.
        """
        # Show warnings for dangerous modes (matches bash behavior)
        if yolo and allow_network:
            warning = """
╔══════════════════════════════════════════════════════╗
║  WARNING: MAXIMUM RISK MODE                          ║
║                                                      ║
║  --yolo + --allow-network = Claude can exfiltrate    ║
║  any file to the internet without confirmation.      ║
║  Only use if you trust the task completely.          ║
╚══════════════════════════════════════════════════════╝
"""
            print(warning, file=sys.stderr)
        elif yolo:
            msg = (
                "Warning: YOLO mode enabled. "
                "Claude can edit files and run commands without confirmation."
            )
            print(msg, file=sys.stderr)
        elif allow_network:
            msg = "Warning: Network access enabled. Data exfiltration is possible."
            print(msg, file=sys.stderr)

        cmd = [
            "podman",
            "run",
            "--rm",
            "-it",
            "--hostname",
            "paude",
        ]

        # Set working directory
        if workdir:
            cmd.extend(["-w", workdir])

        # Add network if specified
        if network:
            cmd.extend(["--network", network])

        # Add mounts
        cmd.extend(mounts)

        # Add environment variables
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Add image
        cmd.append(image)

        # Add YOLO flag if enabled
        if yolo:
            args = ["--dangerously-skip-permissions", *args]

        # Add claude args
        cmd.extend(args)

        result = subprocess.run(cmd)
        return result.returncode

    def run_proxy(
        self,
        image: str,
        network: str,
        dns: str | None = None,
    ) -> str:
        """Start the proxy container.

        Args:
            image: Proxy image to run.
            network: Network to attach to.
            dns: Optional DNS IP for squid to use (passed as SQUID_DNS env var).

        Returns:
            Container name.

        Raises:
            ProxyStartError: If the proxy container fails to start.
        """
        # Generate unique container name using timestamp and counter
        ContainerRunner._proxy_counter += 1
        session_id = f"{int(time.time())}-{ContainerRunner._proxy_counter}"
        container_name = f"paude-proxy-{session_id}"

        # Connect to both internal network and podman network for external access
        cmd = [
            "podman",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "--network",
            f"{network},podman",
        ]

        # Pass DNS IP as environment variable for squid to use
        if dns:
            cmd.extend(["-e", f"SQUID_DNS={dns}"])

        cmd.append(image)

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise ProxyStartError(f"Failed to start proxy: {stderr}")

        # Give proxy time to initialize (matches bash sleep 1)
        time.sleep(1)

        return container_name

    def run_post_create(
        self,
        image: str,
        mounts: list[str],
        env: dict[str, str],
        command: str,
        workdir: str,
        network: str | None = None,
    ) -> bool:
        """Run the postCreateCommand.

        Args:
            image: Container image to use.
            mounts: Volume mount arguments.
            env: Environment variables.
            command: Command to run.
            workdir: Working directory for the command.
            network: Optional network.

        Returns:
            True if successful.
        """
        cmd = [
            "podman",
            "run",
            "--rm",
            "-w",
            workdir,
        ]

        if network:
            cmd.extend(["--network", network])

        cmd.extend(mounts)

        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Use /bin/bash to match bash implementation
        cmd.extend([image, "/bin/bash", "-c", command])

        result = subprocess.run(cmd)
        return result.returncode == 0

    def stop_container(self, name: str) -> None:
        """Stop a container immediately using SIGKILL.

        Uses 'podman kill' instead of 'podman stop' for immediate exit.
        This matches the bash implementation which uses kill for cleanup.

        Args:
            name: Container name.
        """
        subprocess.run(
            ["podman", "kill", name],
            capture_output=True,
        )
