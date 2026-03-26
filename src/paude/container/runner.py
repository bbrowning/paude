"""Container execution for paude."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from paude.container.engine import ContainerEngine


class ContainerNotFoundError(Exception):
    """Container not found."""

    pass


class ContainerRunner:
    """Runs paude containers."""

    def __init__(self, engine: ContainerEngine | None = None) -> None:
        self._engine = engine or ContainerEngine()

    @property
    def engine(self) -> ContainerEngine:
        """Access the underlying container engine."""
        return self._engine

    def create_secret(self, name: str, source_file: Path) -> None:
        """(Re)Create a container secret from a file.

        Skips silently when the engine does not support standalone secrets
        (e.g. Docker without Swarm).

        Args:
            name: Secret name.
            source_file: Path to the source file.
        """
        if not self._engine.supports_secrets:
            return

        try:
            self._engine.run("secret", "create", name, str(source_file))
        except subprocess.CalledProcessError:
            self.remove_secret(name)
            self._engine.run("secret", "create", name, str(source_file))

    def remove_secret(self, name: str) -> None:
        """Remove a container secret, ignoring errors.

        Skips silently when the engine does not support standalone secrets.
        """
        if not self._engine.supports_secrets:
            return

        self._engine.run("secret", "rm", name, check=False)

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
        secrets: list[str] | None = None,
        gpu: str | None = None,
        dns: list[str] | None = None,
    ) -> str:
        """Create a container without starting it.

        Returns:
            Container ID.
        """
        args: list[str] = [
            "create",
            "--name",
            name,
            "--hostname",
            "paude",
            "-w",
            workdir,
            "-it",
        ]

        if gpu:
            args.extend(self._engine.gpu_args(gpu))

        if network:
            args.extend(["--network", network])

        if dns:
            for server in dns:
                args.extend(["--dns", server])

        if secrets:
            for secret in secrets:
                args.extend(["--secret", secret])

        args.extend(mounts)

        for key, value in env.items():
            args.extend(["-e", f"{key}={value}"])

        if labels:
            for key, value in labels.items():
                args.extend(["--label", f"{key}={value}"])

        if entrypoint:
            args.extend(["--entrypoint", entrypoint])

        args.append(image)

        if command:
            args.extend(command)

        result = self._engine.run(*args, check=False)
        if result.returncode != 0:
            cmd = [self._engine.binary, *args]
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        return result.stdout.strip()

    def start_container(self, name: str) -> None:
        """Start an existing container.

        Raises:
            ContainerNotFoundError: If container doesn't exist.
        """
        result = self._engine.run("start", name, check=False)
        if result.returncode != 0:
            if "no such container" in result.stderr.lower():
                raise ContainerNotFoundError(f"Container not found: {name}")
            raise subprocess.CalledProcessError(
                result.returncode,
                [self._engine.binary, "start", name],
                result.stdout,
                result.stderr,
            )

    def stop_container(self, name: str) -> None:
        """Stop a container gracefully with SIGTERM (1-second timeout)."""
        self._engine.run("stop", "-t", "1", name, check=False)

    def stop_container_graceful(self, name: str, timeout: int = 10) -> None:
        """Stop a container gracefully with timeout."""
        self._engine.run("stop", "-t", str(timeout), name, check=False)

    def remove_container(self, name: str, force: bool = False) -> None:
        """Remove a container."""
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(name)
        self._engine.run(*args, check=False)

    def remove_container_verified(self, name: str) -> None:
        """Remove a container and verify it was actually removed.

        Raises:
            RuntimeError: If the container still exists after removal.
        """
        self.remove_container(name, force=True)
        if self.container_exists(name):
            raise RuntimeError(
                f"Failed to remove container '{name}' — it still exists after 'rm -f'"
            )

    def attach_container(
        self,
        name: str,
        entrypoint: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> int:
        """Attach to a running container.

        Returns:
            Exit code from the attached session.
        """
        if entrypoint:
            args: list[str] = ["exec", "-it"]
            if extra_env:
                for key, value in extra_env.items():
                    args.extend(["-e", f"{key}={value}"])
            args.extend([name, entrypoint])
        else:
            args = ["attach", name]

        return self._engine.run_interactive(*args)

    def exec_container(
        self,
        name: str,
        command: list[str],
        interactive: bool = True,
        tty: bool = True,
    ) -> int:
        """Execute a command in a running container.

        Returns:
            Exit code from the command.
        """
        args: list[str] = ["exec"]
        if interactive:
            args.append("-i")
        if tty:
            args.append("-t")
        args.append(name)
        args.extend(command)

        return self._engine.run_interactive(*args)

    def exec_in_container(
        self,
        name: str,
        command: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a command in a running container and capture output."""
        return self._engine.run("exec", name, *command, check=check)

    def inject_file(
        self,
        name: str,
        content: str,
        target: str,
        user: str = "root",
        owner: str | None = None,
    ) -> None:
        """Write file content into a running container via exec.

        Pipes content through ``docker/podman exec`` so that nothing
        is written to the host filesystem — safe for credentials over SSH.
        """
        import shlex

        parent = shlex.quote(str(Path(target).parent))
        quoted_target = shlex.quote(target)
        parts = [f"mkdir -p {parent}", f"cat > {quoted_target}"]
        if owner:
            parts.append(f"chown {shlex.quote(owner)} {quoted_target}")
        parts.append(f"chmod 600 {quoted_target}")
        self._engine.run(
            "exec",
            "-i",
            "--user",
            user,
            name,
            "sh",
            "-c",
            " && ".join(parts),
            input=content,
        )

    def container_exists(self, name: str) -> bool:
        """Check if a container exists."""
        return self._engine.container_exists(name)

    def container_running(self, name: str) -> bool:
        """Check if a container is running."""
        result = self._engine.run(
            "inspect", "-f", "{{.State.Running}}", name, check=False
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def get_container_state(self, name: str) -> str | None:
        """Get the state of a container."""
        result = self._engine.run(
            "inspect", "-f", "{{.State.Status}}", name, check=False
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def list_containers(
        self,
        label_filter: str | None = None,
        all_containers: bool = True,
    ) -> list[dict[str, Any]]:
        """List containers with optional label filter."""
        args = ["ps", "--format", "json"]
        if all_containers:
            args.append("-a")
        if label_filter:
            args.extend(["--filter", f"label={label_filter}"])

        result = self._engine.run(*args, check=False)
        if result.returncode != 0:
            return []

        return self._parse_container_list(result.stdout)

    @staticmethod
    def _parse_container_list(raw_output: str) -> list[dict[str, Any]]:
        """Parse JSON/NDJSON container list output and normalize labels."""
        try:
            parsed = json.loads(raw_output) if raw_output.strip() else []
        except json.JSONDecodeError:
            # Docker outputs NDJSON (one JSON object per line), not an array
            lines = [ln for ln in raw_output.strip().splitlines() if ln.strip()]
            if not lines:
                return []
            try:
                parsed = [json.loads(line) for line in lines]
            except json.JSONDecodeError:
                return []

        # Podman returns a list, Docker may return a single dict
        if isinstance(parsed, dict):
            parsed = [parsed]

        # Docker returns Labels as "k=v,k2=v2" string; normalize to dict
        for container in parsed:
            labels = container.get("Labels")
            if isinstance(labels, str):
                label_dict: dict[str, str] = {}
                if labels:
                    for pair in labels.split(","):
                        k, _, v = pair.partition("=")
                        label_dict[k] = v
                container["Labels"] = label_dict

        return parsed

    def get_container_image(self, name: str) -> str | None:
        """Get the image name of a container."""
        result = self._engine.run(
            "inspect", "-f", self._engine.image_name_format, name, check=False
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None

    def get_container_env(self, name: str, var_name: str) -> str | None:
        """Get an environment variable from a container's config."""
        result = self._engine.run(
            "inspect", "-f", "{{json .Config.Env}}", name, check=False
        )
        if result.returncode != 0:
            return None

        try:
            env_list = json.loads(result.stdout.strip())
            prefix = f"{var_name}="
            for entry in env_list:
                if entry.startswith(prefix):
                    return str(entry[len(prefix) :])
        except (json.JSONDecodeError, TypeError):
            pass

        return None

    def run_post_create(
        self,
        image: str,
        mounts: list[str],
        env: dict[str, str],
        command: str,
        workdir: str,
        network: str | None = None,
        dns: list[str] | None = None,
    ) -> bool:
        """Run the postCreateCommand.

        Returns:
            True if successful.
        """
        args: list[str] = [
            "run",
            "--rm",
            "-w",
            workdir,
        ]

        if network:
            args.extend(["--network", network])

        if dns:
            for server in dns:
                args.extend(["--dns", server])

        args.extend(mounts)

        for key, value in env.items():
            args.extend(["-e", f"{key}={value}"])

        args.extend([image, "/bin/bash", "-c", command])

        return self._engine.run_interactive(*args) == 0
