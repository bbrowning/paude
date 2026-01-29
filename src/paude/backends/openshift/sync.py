"""Configuration synchronization for OpenShift pods."""

from __future__ import annotations

import sys
from pathlib import Path

from paude.backends.openshift.exceptions import OcTimeoutError, OpenShiftError
from paude.backends.openshift.oc import (
    OC_EXEC_TIMEOUT,
    RSYNC_MAX_RETRIES,
    RSYNC_TIMEOUT,
    OcClient,
)
from paude.backends.openshift.resources import CLAUDE_EXCLUDES


class ConfigSyncer:
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
        config_path = "/credentials"
        result = self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "test", "-f", f"{config_path}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        return result.returncode == 0

    def sync_credentials(self, pod_name: str, verbose: bool = False) -> None:
        """Refresh gcloud credentials on the pod (fast, every connect).

        Only syncs gcloud credential files. Used on reconnects when full
        config is already present.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
        """
        home = Path.home()
        config_path = "/credentials"

        print("Refreshing credentials...", file=sys.stderr)

        gcloud_dir = home / ".config" / "gcloud"
        gcloud_files = [
            "application_default_credentials.json",
            "credentials.db",
            "access_tokens.db",
        ]
        gcloud_dest = f"{pod_name}:{config_path}/gcloud"
        for filename in gcloud_files:
            filepath = gcloud_dir / filename
            if filepath.exists():
                try:
                    self._oc.run(
                        "cp", str(filepath), f"{gcloud_dest}/{filename}",
                        "-n", self._namespace, check=False,
                    )
                except Exception:  # noqa: S110
                    pass

        self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "touch", f"{config_path}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        if verbose:
            print("  Refreshed gcloud credentials", file=sys.stderr)
        print("Credentials refreshed.", file=sys.stderr)

    def _prepare_config_directory(self, pod_name: str) -> None:
        """Prepare the config directory on the pod."""
        config_path = "/credentials"
        prep_result = self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "bash", "-c",
            f"mkdir -p {config_path}/gcloud {config_path}/claude && "
            f"(chmod -R g+rwX {config_path} 2>/dev/null || true)",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if prep_result.returncode != 0:
            raise OpenShiftError(
                f"Failed to prepare config directory: {prep_result.stderr}"
            )

    def _sync_gcloud_credentials(self, pod_name: str) -> None:
        """Sync gcloud credentials to the pod."""
        home = Path.home()
        config_path = "/credentials"
        gcloud_dir = home / ".config" / "gcloud"
        gcloud_files = [
            "application_default_credentials.json",
            "credentials.db",
            "access_tokens.db",
        ]
        gcloud_dest = f"{pod_name}:{config_path}/gcloud"
        for filename in gcloud_files:
            filepath = gcloud_dir / filename
            if filepath.exists():
                try:
                    self._oc.run(
                        "cp", str(filepath), f"{gcloud_dest}/{filename}",
                        "-n", self._namespace, check=False,
                    )
                except Exception:  # noqa: S110
                    pass

    def _sync_claude_config(
        self,
        pod_name: str,
        verbose: bool = False,
    ) -> bool:
        """Sync Claude config directory to the pod.

        Returns True if sync succeeded.
        """
        home = Path.home()
        config_path = "/credentials"
        claude_dir = home / ".claude"
        claude_json = home / ".claude.json"

        if claude_dir.is_dir():
            exclude_args = []
            for pattern in CLAUDE_EXCLUDES:
                exclude_args.extend(["--exclude", pattern])

            rsync_success = self.rsync_with_retry(
                f"{claude_dir}/",
                f"{pod_name}:{config_path}/claude",
                exclude_args,
                verbose=verbose,
            )

            if rsync_success:
                self._rewrite_plugin_paths(pod_name, config_path)
                if verbose:
                    print("  Synced ~/.claude/ (including plugins)", file=sys.stderr)
            else:
                print(
                    "  Warning: Failed to sync ~/.claude/ - plugins may not work",
                    file=sys.stderr,
                )
                return False

        if claude_json.exists():
            try:
                dest = f"{pod_name}:{config_path}/claude/claude.json"
                self._oc.run(
                    "cp", str(claude_json), dest, "-n", self._namespace, check=False,
                )
            except Exception:  # noqa: S110
                pass

        return True

    def _sync_gitconfig(self, pod_name: str, verbose: bool = False) -> None:
        """Sync gitconfig to the pod."""
        home = Path.home()
        config_path = "/credentials"
        gitconfig = home / ".gitconfig"
        if gitconfig.exists():
            try:
                self._oc.run(
                    "cp", str(gitconfig), f"{pod_name}:{config_path}/gitconfig",
                    "-n", self._namespace, check=False,
                )
                if verbose:
                    print("  Synced ~/.gitconfig", file=sys.stderr)
            except Exception:  # noqa: S110
                pass

    def _sync_global_gitignore(self, pod_name: str, verbose: bool = False) -> None:
        """Sync global gitignore to the pod."""
        home = Path.home()
        config_path = "/credentials"
        global_gitignore = home / ".config" / "git" / "ignore"
        if global_gitignore.exists():
            try:
                self._oc.run(
                    "cp", str(global_gitignore),
                    f"{pod_name}:{config_path}/gitignore-global",
                    "-n", self._namespace, check=False,
                )
                if verbose:
                    print("  Synced ~/.config/git/ignore (global gitignore)",
                          file=sys.stderr)
            except Exception:  # noqa: S110
                pass

    def _finalize_sync(self, pod_name: str) -> None:
        """Finalize sync by setting permissions and creating .ready marker."""
        config_path = "/credentials"
        self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "bash", "-c",
            f"(chmod -R g+rX {config_path} 2>/dev/null || true) && "
            f"touch {config_path}/.ready && "
            f"chmod g+r {config_path}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        verify_result = self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "test", "-f", f"{config_path}/.ready",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if verify_result.returncode != 0:
            print(
                f"Warning: Failed to create {config_path}/.ready marker",
                file=sys.stderr,
            )

    def sync_full_config(self, pod_name: str, verbose: bool = False) -> None:
        """Sync all configuration to pod /credentials/ directory.

        Full sync including gcloud credentials, claude config, gitconfig,
        and global gitignore.

        Args:
            pod_name: Name of the pod to sync to.
            verbose: Whether to show sync progress.
        """
        print("Syncing configuration to pod...", file=sys.stderr)

        self._prepare_config_directory(pod_name)
        self._sync_gcloud_credentials(pod_name)
        self._sync_claude_config(pod_name, verbose=verbose)
        self._sync_gitconfig(pod_name, verbose=verbose)
        self._sync_global_gitignore(pod_name, verbose=verbose)
        self._finalize_sync(pod_name)

        print("Configuration synced.", file=sys.stderr)

    def _rewrite_plugin_paths(self, pod_name: str, config_path: str) -> None:
        """Rewrite absolute paths in plugin metadata files using jq.

        Claude Code writes plugin paths as absolute host paths. These need
        to be rewritten to container paths.
        """
        container_plugins_path = "/home/paude/.claude/plugins"

        installed_plugins = f"{config_path}/claude/plugins/installed_plugins.json"
        jq_expr = (
            '.plugins |= with_entries(.value |= map('
            'if .installPath then '
            '.installPath = ($prefix + "/" + '
            '(.installPath | split("/") | .[-3:] | join("/"))) '
            'else . end))'
        )
        self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "bash", "-c",
            f'if [ -f "{installed_plugins}" ]; then '
            f'jq --arg prefix "{container_plugins_path}/cache" \'{jq_expr}\' '
            f'"{installed_plugins}" > "{installed_plugins}.tmp" && '
            f'mv "{installed_plugins}.tmp" "{installed_plugins}"; fi',
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )

        known_marketplaces = f"{config_path}/claude/plugins/known_marketplaces.json"
        jq_expr2 = (
            'with_entries(if .value.installLocation then '
            '.value.installLocation = ($prefix + "/marketplaces/" + .key) '
            'else . end)'
        )
        self._oc.run(
            "exec", pod_name, "-n", self._namespace, "--",
            "bash", "-c",
            f'if [ -f "{known_marketplaces}" ]; then '
            f'jq --arg prefix "{container_plugins_path}" \'{jq_expr2}\' '
            f'"{known_marketplaces}" > "{known_marketplaces}.tmp" && '
            f'mv "{known_marketplaces}.tmp" "{known_marketplaces}"; fi',
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
