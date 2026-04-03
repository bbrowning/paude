"""Session connection and exec operations for OpenShift backend."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.oc import OC_EXEC_TIMEOUT, RSYNC_TIMEOUT, OcClient
from paude.backends.openshift.port_forward import (
    PortForwardResult,
    launch_port_forward,
)
from paude.backends.openshift.session_lookup import SessionLookup
from paude.backends.port_forward_utils import check_running_pid, log_file
from paude.backends.shared import (
    PAUDE_LABEL_AGENT,
    PAUDE_LABEL_PROVIDER,
    resource_name,
)


def _format_exit_reason(retcode: int) -> str:
    """Format a process exit code into a human-readable reason."""
    if retcode < 0:
        sig_num = -retcode
        try:
            sig_name = signal.Signals(sig_num).name
        except ValueError:
            sig_name = f"signal {sig_num}"
        return f"killed by {sig_name}"
    return f"exited with code {retcode}"


def _monitor_port_forward(
    pf_result: PortForwardResult,
    session_name: str,
    stop_event: threading.Event,
    check_interval: float = 5.0,
    max_restarts: int = 10,
    restart_delay: float = 2.0,
) -> None:
    """Background thread that watches the port-forward process.

    When the process dies unexpectedly (stop_event not set), silently
    restarts it.  Only shows a warning if restarts are exhausted.
    """
    current_proc = pf_result.proc
    restarts = 0

    while not stop_event.is_set():
        stop_event.wait(check_interval)
        if stop_event.is_set():
            return
        retcode = current_proc.poll()
        if retcode is None:
            continue

        # Process died — try to restart silently
        if restarts < max_restarts:
            restarts += 1
            stop_event.wait(restart_delay)
            if stop_event.is_set():
                return
            current_proc = launch_port_forward(
                pf_result.cmd, pf_result.log_path, session_name
            )
            continue

        # Restarts exhausted — warn the user
        lf = log_file(session_name)
        reason = _format_exit_reason(retcode)
        log_hint = f"\r\n[paude] Log: {lf}" if lf.exists() else ""
        msg = (
            f"\r\n\033[33m[paude] WARNING: port-forward for "
            f"'{session_name}' {reason}{log_hint}\033[0m\r\n"
        )
        sys.stderr.write(msg)
        sys.stderr.flush()
        return


def _show_port_forward_diagnostics(
    session_name: str,
    proc: subprocess.Popen[bytes] | None,
) -> None:
    """After disconnect, show port-forward diagnostics if it died."""
    if proc is None:
        return

    # If a restart happened, the original proc is dead but a new one is
    # running via the PID file — suppress misleading diagnostics.
    if check_running_pid(session_name):
        return

    retcode = proc.poll()
    if retcode is None:
        return

    reason = _format_exit_reason(retcode)
    print(
        f"\n--- Port-forward for '{session_name}' {reason} ---",
        file=sys.stderr,
    )

    lf = log_file(session_name)
    if lf.exists():
        content = lf.read_text()
        lines = content.strip().splitlines()
        if lines:
            print(f"Log ({lf}):", file=sys.stderr)
            for line in lines[-20:]:
                print(f"  {line}", file=sys.stderr)
        else:
            print("Log file was empty.", file=sys.stderr)

    print(
        "Run 'paude connect' again to re-establish port-forwarding.",
        file=sys.stderr,
    )
    print("---", file=sys.stderr)


class SessionConnector:
    """Handles connecting to and executing commands in running sessions."""

    def __init__(
        self,
        oc: OcClient,
        namespace: str,
        config: OpenShiftConfig,
        lookup: SessionLookup,
    ) -> None:
        self._oc = oc
        self._namespace = namespace
        self._config = config
        self._lookup = lookup

    def connect_session(self, name: str, github_token: str | None = None) -> int:
        """Attach to a running session.

        Returns:
            Exit code from the attached session.
        """
        pname, ns = self._verify_pod_running(name)
        if pname is None:
            return 1

        pf_result, port_urls = self._start_port_forward(name, pname)

        stop_event = threading.Event()
        if pf_result is not None:
            monitor = threading.Thread(
                target=_monitor_port_forward,
                args=(pf_result, name, stop_event),
                daemon=True,
            )
            monitor.start()

        try:
            return self._attach_to_pod(pname, name, ns, port_urls=port_urls)
        finally:
            stop_event.set()
            _show_port_forward_diagnostics(name, pf_result.proc if pf_result else None)
            self._stop_port_forward(name)

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

    def _start_port_forward(
        self, session_name: str, pod_name: str
    ) -> tuple[PortForwardResult | None, list[str]]:
        """Start port-forwarding if the agent has exposed ports.

        Returns:
            Tuple of (PortForwardResult or None, list of port-forward URL strings).
        """
        from paude.agents import get_agent
        from paude.backends.openshift.port_forward import PortForwardManager

        sts = self._lookup.get_statefulset(session_name)
        agent_name = self._agent_name_from_sts(sts)
        provider = self._provider_from_sts(sts)
        agent = get_agent(agent_name, provider=provider)
        ports = agent.config.exposed_ports
        if not ports:
            return None, []

        mgr = PortForwardManager(self._namespace, self._config.context)
        result = mgr.start(session_name, pod_name, ports)

        return result, [f"http://localhost:{hp}" for hp, _cp in ports]

    def _stop_port_forward(self, session_name: str) -> None:
        """Stop any active port-forward for this session."""
        from paude.backends.port_forward_utils import stop_port_forward

        stop_port_forward(session_name)

    @staticmethod
    def _agent_name_from_sts(sts: dict[str, object] | None) -> str:
        """Extract the agent name from a StatefulSet's labels."""
        if not sts:
            return "claude"
        metadata: dict[str, object] = sts.get("metadata", {})  # type: ignore[assignment]
        labels: dict[str, object] = metadata.get("labels", {})  # type: ignore[assignment]
        return str(labels.get(PAUDE_LABEL_AGENT, "claude"))

    @staticmethod
    def _provider_from_sts(sts: dict[str, object] | None) -> str | None:
        """Extract the provider name from a StatefulSet's labels."""
        if not sts:
            return None
        metadata: dict[str, object] = sts.get("metadata", {})  # type: ignore[assignment]
        labels: dict[str, object] = metadata.get("labels", {})  # type: ignore[assignment]
        value = labels.get(PAUDE_LABEL_PROVIDER)
        return str(value) if value is not None else None

    def _get_session_agent_name(self, session_name: str) -> str:
        """Look up the agent name from StatefulSet labels."""
        return self._lookup.get_session_agent_name(session_name)

    def _read_openclaw_token(self, pname: str, ns: str) -> str | None:
        """Read the OpenClaw auth token from the pod's config file."""
        from paude.backends.shared import OPENCLAW_AUTH_READER_SCRIPT

        result = self._oc.run(
            "exec",
            pname,
            "-n",
            ns,
            "--",
            "python3",
            "-c",
            OPENCLAW_AUTH_READER_SCRIPT,
            check=False,
            timeout=OC_EXEC_TIMEOUT,
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            return token if token else None
        return None

    def _attach_to_pod(
        self,
        pname: str,
        name: str,
        ns: str,
        port_urls: list[str] | None = None,
    ) -> int:
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

        exec_cmd = self._build_exec_cmd(pname, ns, port_urls=port_urls)
        exec_result = subprocess.run(exec_cmd)

        os.system("stty sane 2>/dev/null")  # noqa: S605

        if port_urls:
            from paude.backends.shared import enrich_port_url

            agent_name = self._get_session_agent_name(name)
            token = None
            if agent_name == "openclaw":
                token = self._read_openclaw_token(pname, ns)
            for url in port_urls:
                print(
                    f"Port-forward active: {enrich_port_url(url, token)}",
                    file=sys.stderr,
                )

        return exec_result.returncode

    def _build_exec_cmd(
        self,
        pname: str,
        ns: str,
        port_urls: list[str] | None = None,
    ) -> list[str]:
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

        if port_urls:
            cmd.extend(["env", f"PAUDE_PORT_URLS={';'.join(port_urls)}"])

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
