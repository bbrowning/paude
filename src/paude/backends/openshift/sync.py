"""Configuration synchronization for OpenShift pods."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from paude.backends.openshift.exceptions import OcTimeoutError, OpenShiftError
from paude.backends.openshift.oc import (
    OC_EXEC_TIMEOUT,
    RSYNC_MAX_RETRIES,
    RSYNC_TIMEOUT,
    OcClient,
)
from paude.backends.sync_base import CONFIG_PATH, BaseConfigSyncer
from paude.constants import CONTAINER_HOME, GCP_ADC_FILENAME

if TYPE_CHECKING:
    from paude.agents.base import Agent


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

    def _copy_dir(
        self,
        local_dir: str,
        container_path: str,
        *,
        excludes: list[str] | None = None,
        context: str,
    ) -> bool:
        exclude_args: list[str] = []
        if excludes:
            for pattern in excludes:
                exclude_args.extend(["--exclude", pattern])

        success = self.rsync_with_retry(
            f"{local_dir}/",
            f"{self._target}:{container_path}",
            exclude_args,
        )
        if not success:
            print(
                f"  Warning: Failed to {context} ({local_dir}/) - plugins may not work",
                file=sys.stderr,
            )
        return success

    def _rewrite_plugin_paths(self, agent_path: str, agent: Agent, home: Path) -> None:
        config_dir_name = agent.config.config_dir_name
        container_plugins_path = f"{CONTAINER_HOME}/{config_dir_name}/plugins"

        installed_plugins = f"{agent_path}/plugins/installed_plugins.json"
        jq_expr = (
            ".plugins |= with_entries(.value |= map("
            "if .installPath then "
            '.installPath = ($prefix + "/" + '
            '(.installPath | split("/") | .[-3:] | join("/"))) '
            "else . end))"
        )
        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            f'if [ -f "{installed_plugins}" ]; then '
            f"jq --arg prefix \"{container_plugins_path}/cache\" '{jq_expr}' "
            f'"{installed_plugins}" > "{installed_plugins}.tmp" && '
            f'mv "{installed_plugins}.tmp" "{installed_plugins}"; fi',
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        known_marketplaces = f"{agent_path}/plugins/known_marketplaces.json"
        jq_expr2 = (
            "with_entries(if .value.installLocation then "
            '.value.installLocation = ($prefix + "/marketplaces/" + .key) '
            "else . end)"
        )
        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            f'if [ -f "{known_marketplaces}" ]; then '
            f"jq --arg prefix \"{container_plugins_path}\" '{jq_expr2}' "
            f'"{known_marketplaces}" > "{known_marketplaces}.tmp" && '
            f'mv "{known_marketplaces}.tmp" "{known_marketplaces}"; fi',
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

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
        github_token: str | None = None,
        agent_name: str = "claude",
        provider: str | None = None,
        secret_env: dict[str, str] | None = None,
        workspace: str = "",
        args: str = "",
    ) -> None:
        """Sync all configuration to pod /credentials/ directory.

        Full sync including gcloud credentials, agent config, gitconfig,
        global gitignore, optional GitHub token, and secret env vars.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
            github_token: Optional GitHub token to inject into pod tmpfs.
                Falls back to PAUDE_GITHUB_TOKEN env var if not provided.
            agent_name: Agent name for config directory naming.
            secret_env: Secret environment variables to sync to tmpfs.
            workspace: Container workspace path for sandbox config.
            args: Agent args string for sandbox config.
        """
        self._target = pod_name

        print("Syncing configuration to pod...", file=sys.stderr)

        self._prepare_config_directory(agent_name=agent_name)
        self._sync_gcloud_credentials()
        self._sync_config_files(agent_name)
        self._sync_github_token(github_token)
        self._sync_secret_env_vars(secret_env or {})
        self._sync_sandbox_config(agent_name, workspace, args, provider=provider)
        self._finalize_sync()

        print("Configuration synced.", file=sys.stderr)

    def sync_credentials(
        self,
        pod_name: str,
        verbose: bool = False,
        github_token: str | None = None,
        secret_env: dict[str, str] | None = None,
        agent_name: str = "claude",
        provider: str | None = None,
        workspace: str = "",
        args: str = "",
    ) -> None:
        """Refresh gcloud credentials on the pod (fast, every connect).

        Only syncs gcloud credential files. Used on reconnects when full
        config is already present. Also syncs GitHub token and secret env
        vars if available.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
            github_token: Optional GitHub token to inject into pod tmpfs.
                Falls back to PAUDE_GITHUB_TOKEN env var if not provided.
            secret_env: Secret environment variables to sync to tmpfs.
            agent_name: Agent name (used for agent-specific credential sync).
            workspace: Container workspace path for sandbox config.
            args: Agent args string for sandbox config.
        """
        self._target = pod_name
        home = Path.home()

        print("Refreshing credentials...", file=sys.stderr)

        gcloud_dir = home / ".config" / "gcloud"
        gcloud_files = [
            GCP_ADC_FILENAME,
            "credentials.db",
            "access_tokens.db",
        ]
        for filename in gcloud_files:
            filepath = gcloud_dir / filename
            if filepath.exists():
                self._copy_file(
                    str(filepath),
                    f"{CONFIG_PATH}/gcloud/{filename}",
                    context=f"refresh gcloud {filename}",
                )

        self._sync_github_token(github_token)
        self._sync_secret_env_vars(secret_env or {})
        self._sync_sandbox_config(agent_name, workspace, args, provider=provider)

        if agent_name == "cursor":
            self._sync_cursor_auth(home)

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
            print("  Refreshed gcloud credentials", file=sys.stderr)
        print("Credentials refreshed.", file=sys.stderr)

    # -- OpenShift-specific internal methods --------------------------------

    def _sync_sandbox_config(
        self,
        agent_name: str,
        workspace: str,
        args: str,
        provider: str | None = None,
    ) -> None:
        """Generate and write agent sandbox config script into the pod."""
        from paude.backends.shared import generate_sandbox_config_script
        from paude.constants import CONTAINER_WORKSPACE

        ws = workspace or CONTAINER_WORKSPACE
        content = generate_sandbox_config_script(
            agent_name, ws, args, provider=provider
        )
        self._cp_content_to_pod(content, "/tmp/agent-sandbox-config.sh")  # noqa: S108

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

    def _sync_secret_env_vars(self, secret_env: dict[str, str]) -> None:
        """Sync secret environment variables to /credentials/env/ on the pod."""
        if not secret_env:
            return

        self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "mkdir",
            "-p",
            f"{CONFIG_PATH}/env",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        for var_name, value in secret_env.items():
            self._cp_content_to_pod(value, f"{CONFIG_PATH}/env/{var_name}")

    def _sync_github_token(self, github_token: str | None) -> None:
        """Sync GitHub token to the pod's credentials directory."""
        token = github_token or os.environ.get("PAUDE_GITHUB_TOKEN")
        if not token:
            return

        self._cp_content_to_pod(token, f"{CONFIG_PATH}/github_token")

    def _prepare_config_directory(self, agent_name: str = "claude") -> None:
        """Prepare the config directory on the pod."""
        prep_result = self._oc.run(
            "exec",
            self._target,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            f"mkdir -p {CONFIG_PATH}/gcloud {CONFIG_PATH}/{agent_name} && "
            f"(chmod -R g+rwX {CONFIG_PATH} 2>/dev/null || true)",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if prep_result.returncode != 0:
            raise OpenShiftError(
                f"Failed to prepare config directory: {prep_result.stderr}"
            )

    def _sync_gcloud_credentials(self) -> None:
        """Sync gcloud credentials to the pod."""
        home = Path.home()
        gcloud_dir = home / ".config" / "gcloud"
        gcloud_files = [
            GCP_ADC_FILENAME,
            "credentials.db",
            "access_tokens.db",
        ]
        for filename in gcloud_files:
            filepath = gcloud_dir / filename
            if filepath.exists():
                self._copy_file(
                    str(filepath),
                    f"{CONFIG_PATH}/gcloud/{filename}",
                    context=f"sync gcloud {filename}",
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
