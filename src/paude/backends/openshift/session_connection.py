"""Session connection and exec operations for OpenShift backend."""

from __future__ import annotations

import os
import subprocess
import sys

from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.oc import OC_EXEC_TIMEOUT, RSYNC_TIMEOUT, OcClient
from paude.backends.openshift.session_lookup import SessionLookup
from paude.backends.openshift.sync import ConfigSyncer
from paude.backends.shared import PAUDE_LABEL_AGENT, resource_name


class SessionConnector:
    """Handles connecting to and executing commands in running sessions."""

    def __init__(
        self,
        oc: OcClient,
        namespace: str,
        config: OpenShiftConfig,
        lookup: SessionLookup,
        syncer: ConfigSyncer,
    ) -> None:
        self._oc = oc
        self._namespace = namespace
        self._config = config
        self._lookup = lookup
        self._syncer = syncer

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        """Attach to a running session.

        On first connect: syncs full configuration.
        On reconnect: only refreshes credentials (fast).

        Returns:
            Exit code from the attached session.
        """
        pname, ns = self._verify_pod_running(name)
        if pname is None:
            return 1

        self._sync_for_connect(pname, name, github_token)
        return self._attach_to_pod(pname, name, ns)

    def _verify_pod_running(self, name: str) -> tuple[str | None, str]:
        """Check pod exists and is in Running phase.

        Returns:
            Tuple of (pod_name_or_None, namespace).
        """
        ns = self._namespace
        pname = self._lookup.get_pod_for_session(name)
        if pname is None:
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return None, ns

        result = self._oc.run(
            "get",
            "pod",
            pname,
            "-n",
            ns,
            "-o",
            "jsonpath={.status.phase}",
            check=False,
        )

        if result.returncode != 0 or result.stdout.strip() != "Running":
            print(f"Session '{name}' is not running.", file=sys.stderr)
            return None, ns

        return pname, ns

    def _sync_for_connect(
        self, pname: str, name: str, github_token: str | None
    ) -> None:
        """Sync credentials/config for a connect operation."""
        from paude.agents import get_agent
        from paude.agents.base import build_secret_environment_from_config

        sts = self._lookup.get_statefulset(name)
        sts_labels = sts.get("metadata", {}).get("labels", {}) if sts else {}
        agent_name = sts_labels.get(PAUDE_LABEL_AGENT, "claude")
        agent_args = self._extract_env_from_sts(sts, "PAUDE_AGENT_ARGS")

        agent = get_agent(agent_name)
        secret_env = build_secret_environment_from_config(agent.config)

        if self._syncer.is_config_synced(pname):
            self._syncer.sync_credentials(
                pname,
                verbose=False,
                github_token=github_token,
                secret_env=secret_env,
                agent_name=agent_name,
                args=agent_args,
            )
        else:
            self._syncer.sync_full_config(
                pname,
                verbose=False,
                github_token=github_token,
                agent_name=agent_name,
                secret_env=secret_env,
                args=agent_args,
            )

    @staticmethod
    def _extract_env_from_sts(sts: dict[str, object] | None, var_name: str) -> str:
        """Extract an env var value from a StatefulSet spec."""
        if not sts:
            return ""
        spec: dict[str, object] = sts.get("spec", {})  # type: ignore[assignment]
        template: dict[str, object] = spec.get("template", {})  # type: ignore[assignment]
        pod_spec: dict[str, object] = template.get("spec", {})  # type: ignore[assignment]
        containers: list[dict[str, object]] = pod_spec.get("containers", [])  # type: ignore[assignment]
        for container in containers:
            env_list: list[dict[str, str]] = container.get("env", [])  # type: ignore[assignment]
            for env in env_list:
                if env.get("name") == var_name:
                    return str(env.get("value", ""))
        return ""

    def _attach_to_pod(self, pname: str, name: str, ns: str) -> int:
        """Check workspace state, build exec command, and attach."""
        check_result = self._oc.run(
            "exec",
            pname,
            "-n",
            ns,
            "--",
            "test",
            "-d",
            "/pvc/workspace/.git",
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if check_result.returncode != 0:
            print("", file=sys.stderr)
            print("Workspace is empty. To sync code:", file=sys.stderr)
            print(f"  paude remote add {name}", file=sys.stderr)
            print(f"  git push {resource_name(name)} main", file=sys.stderr)
            print("", file=sys.stderr)

        exec_cmd = self._build_exec_cmd(pname, ns)
        exec_result = subprocess.run(exec_cmd)

        os.system("stty sane 2>/dev/null")  # noqa: S605

        return exec_result.returncode

    def _build_exec_cmd(self, pname: str, ns: str) -> list[str]:
        """Build the oc exec command list."""
        if self._config.context:
            cmd = [
                "oc",
                "--context",
                self._config.context,
                "exec",
                "-it",
                "-n",
                ns,
                pname,
                "--",
            ]
        else:
            cmd = ["oc", "exec", "-it", "-n", ns, pname, "--"]

        cmd.append("/usr/local/bin/entrypoint-session.sh")
        return cmd

    def exec_in_session(self, name: str, command: str) -> tuple[int, str, str]:
        """Execute a command inside a running session's container.

        Returns:
            Tuple of (return_code, stdout, stderr).

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._lookup.require_running_pod(name)

        result = self._oc.run(
            "exec",
            pname,
            "-n",
            self._namespace,
            "--",
            "bash",
            "-c",
            command,
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        return (result.returncode, result.stdout, result.stderr)

    def copy_to_session(self, name: str, local_path: str, remote_path: str) -> None:
        """Copy a file or directory from local to a running session.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._lookup.require_running_pod(name)

        self._oc.run(
            "cp",
            local_path,
            f"{pname}:{remote_path}",
            "-n",
            self._namespace,
            timeout=RSYNC_TIMEOUT,
        )

    def copy_from_session(self, name: str, remote_path: str, local_path: str) -> None:
        """Copy a file or directory from a running session to local.

        Raises:
            SessionNotFoundError: If session not found.
            ValueError: If session is not running.
        """
        pname = self._lookup.require_running_pod(name)

        self._oc.run(
            "cp",
            f"{pname}:{remote_path}",
            local_path,
            "-n",
            self._namespace,
            timeout=RSYNC_TIMEOUT,
        )
