"""Container execution for paude."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any


class ProxyStartError(Exception):
    """Error starting the proxy container."""

    pass


class ContainerNotFoundError(Exception):
    """Container not found."""

    pass


class VolumeNotFoundError(Exception):
    """Volume not found."""

    pass


# Labels used to identify paude sessions
PAUDE_LABEL_APP = "app=paude"
PAUDE_LABEL_SESSION = "paude.io/session-name"
PAUDE_LABEL_WORKSPACE = "paude.io/workspace"
PAUDE_LABEL_CREATED = "paude.io/created-at"


class ContainerRunner:
    """Runs paude containers."""

    _proxy_counter = 0

    def create_container(
        self,
        name: str,
        image: str,
        mounts: list[str],
        env: dict[str, str],
        workdir: str,
        network: str | None = None,
        labels: dict[str, str] | None = None,
        entrypoint: str | None = None,
        command: list[str] | None = None,
    ) -> str:
        """Create a container without starting it.

        Args:
            name: Container name.
            image: Container image to run.
            mounts: Volume mount arguments.
            env: Environment variables.
            workdir: Working directory inside the container.
            network: Optional network to attach to.
            labels: Labels to attach to the container.
            entrypoint: Optional entrypoint override.
            command: Optional command to run (after image in podman create).

        Returns:
            Container ID.

        Raises:
            subprocess.CalledProcessError: If container creation fails.
        """
        cmd = [
            "podman",
            "create",
            "--name", name,
            "--hostname", "paude",
            "-w", workdir,
            "-it",
        ]

        if network:
            cmd.extend(["--network", network])

        cmd.extend(mounts)

        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        if labels:
            for key, value in labels.items():
                cmd.extend(["--label", f"{key}={value}"])

        if entrypoint:
            cmd.extend(["--entrypoint", entrypoint])

        cmd.append(image)

        if command:
            cmd.extend(command)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        return result.stdout.strip()

    def start_container(self, name: str) -> None:
        """Start an existing container.

        Args:
            name: Container name.

        Raises:
            ContainerNotFoundError: If container doesn't exist.
        """
        result = subprocess.run(
            ["podman", "start", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if "no such container" in result.stderr.lower():
                raise ContainerNotFoundError(f"Container not found: {name}")
            raise subprocess.CalledProcessError(
                result.returncode, ["podman", "start", name],
                result.stdout, result.stderr
            )

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

    def stop_container_graceful(self, name: str, timeout: int = 10) -> None:
        """Stop a container gracefully with timeout.

        Args:
            name: Container name.
            timeout: Seconds to wait before SIGKILL.
        """
        subprocess.run(
            ["podman", "stop", "-t", str(timeout), name],
            capture_output=True,
        )

    def remove_container(self, name: str, force: bool = False) -> None:
        """Remove a container.

        Args:
            name: Container name.
            force: Force removal even if running.
        """
        cmd = ["podman", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)

        subprocess.run(cmd, capture_output=True)

    def attach_container(
        self,
        name: str,
        entrypoint: str | None = None,
    ) -> int:
        """Attach to a running container.

        Args:
            name: Container name.
            entrypoint: Optional command to exec into.

        Returns:
            Exit code from the attached session.
        """
        if entrypoint:
            cmd = ["podman", "exec", "-it", name, entrypoint]
        else:
            cmd = ["podman", "attach", name]

        result = subprocess.run(cmd)
        return result.returncode

    def exec_container(
        self,
        name: str,
        command: list[str],
        interactive: bool = True,
        tty: bool = True,
    ) -> int:
        """Execute a command in a running container.

        Args:
            name: Container name.
            command: Command to execute.
            interactive: Enable interactive mode.
            tty: Allocate a TTY.

        Returns:
            Exit code from the command.
        """
        cmd = ["podman", "exec"]
        if interactive:
            cmd.append("-i")
        if tty:
            cmd.append("-t")
        cmd.append(name)
        cmd.extend(command)

        result = subprocess.run(cmd)
        return result.returncode

    def container_exists(self, name: str) -> bool:
        """Check if a container exists.

        Args:
            name: Container name.

        Returns:
            True if container exists.
        """
        result = subprocess.run(
            ["podman", "container", "exists", name],
            capture_output=True,
        )
        return result.returncode == 0

    def container_running(self, name: str) -> bool:
        """Check if a container is running.

        Args:
            name: Container name.

        Returns:
            True if container is running.
        """
        result = subprocess.run(
            ["podman", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def get_container_state(self, name: str) -> str | None:
        """Get the state of a container.

        Args:
            name: Container name.

        Returns:
            Container state string or None if not found.
        """
        result = subprocess.run(
            ["podman", "inspect", "-f", "{{.State.Status}}", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def list_containers(
        self,
        label_filter: str | None = None,
        all_containers: bool = True,
    ) -> list[dict[str, Any]]:
        """List containers with optional label filter.

        Args:
            label_filter: Label filter (e.g., "app=paude").
            all_containers: Include stopped containers.

        Returns:
            List of container info dictionaries.
        """
        cmd = ["podman", "ps", "--format", "json"]
        if all_containers:
            cmd.append("-a")
        if label_filter:
            cmd.extend(["--filter", f"label={label_filter}"])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        try:
            return json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            return []

    def create_volume(self, name: str, labels: dict[str, str] | None = None) -> str:
        """Create a named volume.

        Args:
            name: Volume name.
            labels: Labels to attach to the volume.

        Returns:
            Volume name.
        """
        cmd = ["podman", "volume", "create"]
        if labels:
            for key, value in labels.items():
                cmd.extend(["--label", f"{key}={value}"])
        cmd.append(name)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        return result.stdout.strip()

    def remove_volume(self, name: str, force: bool = False) -> None:
        """Remove a named volume.

        Args:
            name: Volume name.
            force: Force removal.
        """
        cmd = ["podman", "volume", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(name)

        subprocess.run(cmd, capture_output=True)

    def volume_exists(self, name: str) -> bool:
        """Check if a volume exists.

        Args:
            name: Volume name.

        Returns:
            True if volume exists.
        """
        result = subprocess.run(
            ["podman", "volume", "exists", name],
            capture_output=True,
        )
        return result.returncode == 0

    def get_volume_labels(self, name: str) -> dict[str, str]:
        """Get labels from a volume.

        Args:
            name: Volume name.

        Returns:
            Dictionary of labels.
        """
        result = subprocess.run(
            ["podman", "volume", "inspect", "-f", "{{json .Labels}}", name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return {}

        try:
            labels = json.loads(result.stdout) if result.stdout.strip() else {}
            return labels if labels else {}
        except json.JSONDecodeError:
            return {}

    def list_volumes(
        self,
        label_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List volumes with optional label filter.

        Args:
            label_filter: Label filter (e.g., "app=paude").

        Returns:
            List of volume info dictionaries.
        """
        cmd = ["podman", "volume", "ls", "--format", "json"]
        if label_filter:
            cmd.extend(["--filter", f"label={label_filter}"])

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return []

        try:
            return json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            return []

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
        """Run the Claude container (ephemeral, legacy mode).

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
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  WARNING: MAXIMUM RISK MODE                          \u2551
\u2551                                                      \u2551
\u2551  --yolo + --allow-network = Claude can exfiltrate    \u2551
\u2551  any file to the internet without confirmation.      \u2551
\u2551  Only use if you trust the task completely.          \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
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
