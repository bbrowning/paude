"""Configuration synchronization for OpenShift pods."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from paude.backends.openshift.exceptions import OcTimeoutError, OpenShiftError
from paude.backends.openshift.oc import (
    OC_EXEC_TIMEOUT,
    RSYNC_MAX_RETRIES,
    RSYNC_TIMEOUT,
    OcClient,
)
from paude.backends.sync_base import CONFIG_PATH, BaseConfigSyncer
from paude.constants import GCP_ADC_FILENAME, SANDBOX_CONFIG_TARGET


class ConfigSyncer(BaseConfigSyncer):
    """Handles configuration and credential synchronization to OpenShift pods.

    This class is responsible for syncing local configuration files
    (gcloud credentials, claude config, gitconfig) to remote pods.
    """

    def __init__(self, oc: OcClient, namespace: str) -> None:
        """Initialize the ConfigSyncer.

        Args:
            oc: OcClient instance for running oc commands.
            namespace: Kubernetes namespace for operations.
        """
        self._oc = oc
        self._namespace = namespace
        self._target = ""

    # -- BaseConfigSyncer transport implementation -------------------------

    def _copy_file(self, local_path: str, container_path: str, *, context: str) -> bool:
        try:
            self._oc.run(
                "cp",
                local_path,
                f"{self._target}:{container_path}",
                "-n",
                self._namespace,
                check=False,
            )
            return True
        except Exception:  # noqa: S110
            return False

    # -- OpenShift-specific public API -------------------------------------

    def rsync_with_retry(
        self,
        source: str,
        dest: str,
        exclude_args: list[str],
        verbose: bool = False,
        delete: bool = False,
    ) -> bool:
        """Run oc rsync with retry logic for timeouts.

        Args:
            source: Source path (local or pod:path format).
            dest: Destination path (local or pod:path format).
            exclude_args: List of --exclude arguments.
            verbose: Whether to show rsync output (default False).
            delete: Whether to delete files not in source (default False).

        Returns:
            True if sync succeeded, False if all retries failed.
        """
        for attempt in range(1, RSYNC_MAX_RETRIES + 1):
            try:
                rsync_args = [
                    "rsync",
                    "--progress",
                    source,
                    dest,
                    "--no-perms",
                ]
                if delete:
                    rsync_args.append("--delete")
                rsync_args.extend(exclude_args)

                result = self._oc.run(
                    *rsync_args,
                    timeout=RSYNC_TIMEOUT,
                    capture=True,
                    check=False,
                    namespace=self._namespace,
                )

                if verbose and result.stdout:
                    print(result.stdout, file=sys.stderr)

                if result.returncode != 0:
                    print(
                        f"Rsync failed: {result.stderr.strip() or 'unknown error'}",
                        file=sys.stderr,
                    )
                    return False

                return True
            except OcTimeoutError:
                if attempt < RSYNC_MAX_RETRIES:
                    print(
                        f"Rsync timed out (attempt {attempt}/{RSYNC_MAX_RETRIES}), "
                        "retrying...",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Rsync failed after {RSYNC_MAX_RETRIES} attempts",
                        file=sys.stderr,
                    )
                    return False
        return False

    def is_config_synced(self, pod_name: str) -> bool:
        """Check if configuration has already been synced to the pod.

        Returns True if /credentials/.ready exists, indicating a previous
        full config sync.
        """
        result = self._oc.run(
            "exec",
            pod_name,
            "-n",
            self._namespace,
            "--",
            "test",
            "-f",
            f"{CONFIG_PATH}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        return result.returncode == 0

    def sync_full_config(
        self,
        pod_name: str,
        verbose: bool = False,
        agent_name: str = "claude",
        provider: str | None = None,
        workspace: str = "",
        args: str = "",
        yolo: bool = False,
    ) -> None:
        """Sync all configuration to pod /credentials/ directory.

        Full sync including stub gcloud credentials, gitconfig,
        global gitignore, and sandbox config. No real credentials are synced —
        all authentication is handled by the proxy sidecar.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
            agent_name: Agent name for config directory naming.
            workspace: Container workspace path for sandbox config.
            args: Agent args string for sandbox config.
            yolo: Whether YOLO mode is enabled.
        """
        self._target = pod_name

        print("Syncing configuration to pod...", file=sys.stderr)

        self._prepare_config_directory()
        self._sync_stub_gcloud_credentials()
        self._sync_config_files(agent_name)
        self._sync_sandbox_config(
            agent_name, workspace, args, provider=provider, yolo=yolo
        )
        self._finalize_sync()

        print("Configuration synced.", file=sys.stderr)

    def sync_credentials(
        self,
        pod_name: str,
        verbose: bool = False,
        agent_name: str = "claude",
        provider: str | None = None,
        workspace: str = "",
        args: str = "",
        yolo: bool = False,
    ) -> None:
        """Refresh stub credentials and config on the pod (fast, every connect).

        Syncs stub gcloud credentials and sandbox config. No real credentials
        are synced — all authentication is handled by the proxy sidecar.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
            agent_name: Agent name (used for agent-specific credential sync).
            workspace: Container workspace path for sandbox config.
            args: Agent args string for sandbox config.
            yolo: Whether YOLO mode is enabled.
        """
        self._target = pod_name

        print("Refreshing credentials...", file=sys.stderr)

        self._sync_stub_gcloud_credentials()
        self._sync_sandbox_config(
            agent_name, workspace, args, provider=provider, yolo=yolo
        )

        self._oc.run(
            "exec",
            pod_name,
            "-n",
            self._namespace,
            "--",
            "touch",
            f"{CONFIG_PATH}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        if verbose:
            print("  Refreshed stub gcloud credentials", file=sys.stderr)
        print("Credentials refreshed.", file=sys.stderr)

    # -- OpenShift-specific internal methods --------------------------------

    def _sync_sandbox_config(
        self,
        agent_name: str,
        workspace: str,
        args: str,
        provider: str | None = None,
        yolo: bool = False,
    ) -> None:
        """Generate and write agent sandbox config script into the pod."""
        from paude.backends.shared import generate_sandbox_config_script
        from paude.constants import CONTAINER_WORKSPACE

        ws = workspace or CONTAINER_WORKSPACE
        content = generate_sandbox_config_script(
            agent_name, ws, args, provider=provider, yolo=yolo
        )
        parent = str(Path(SANDBOX_CONFIG_TARGET).parent)
        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "mkdir",
            "-p",
            parent,
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        self._cp_content_to_pod(content, SANDBOX_CONFIG_TARGET)

    def _cp_content_to_pod(self, content: str, dest_path: str) -> None:
        """Write content to a tempfile and copy it to the pod via ``oc cp``."""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".secret") as tmp:
                tmp.write(content)
                tmp.flush()
                self._oc.run(
                    "cp",
                    tmp.name,
                    f"{self._target}:{dest_path}",
                    "-n",
                    self._namespace,
                    check=False,
                )
        except Exception as exc:
            print(
                f"Warning: failed to copy content to {dest_path}: {exc}",
                file=sys.stderr,
            )

    def _prepare_config_directory(self) -> None:
        """Prepare the config directory on the pod."""
        prep_result = self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            f"mkdir -p {CONFIG_PATH}/gcloud && "
            f"(chmod -R g+rwX {CONFIG_PATH} 2>/dev/null || true)",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if prep_result.returncode != 0:
            raise OpenShiftError(
                f"Failed to prepare config directory: {prep_result.stderr}"
            )

    def _sync_stub_gcloud_credentials(self) -> None:
        """Sync stub GCP ADC to the pod (proxy handles real auth)."""
        from paude.backends.shared import STUB_ADC_JSON

        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "mkdir",
            "-p",
            f"{CONFIG_PATH}/gcloud",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        self._cp_content_to_pod(
            STUB_ADC_JSON, f"{CONFIG_PATH}/gcloud/{GCP_ADC_FILENAME}"
        )

    def _finalize_sync(self) -> None:
        """Finalize sync by setting permissions and creating .ready marker."""
        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            f"(chmod -R g+rX {CONFIG_PATH} 2>/dev/null || true) && "
            f"touch {CONFIG_PATH}/.ready && "
            f"chmod g+r {CONFIG_PATH}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        verify_result = self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "test",
            "-f",
            f"{CONFIG_PATH}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if verify_result.returncode != 0:
            print(
                f"Warning: Failed to create {CONFIG_PATH}/.ready marker",
                file=sys.stderr,
            )
