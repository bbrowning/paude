"""OcClient wrapper for oc CLI commands."""

from __future__ import annotations

import subprocess

from paude.backends.openshift.config import OpenShiftConfig
from paude.backends.openshift.exceptions import (
    NamespaceNotFoundError,
    OcNotInstalledError,
    OcNotLoggedInError,
    OcTimeoutError,
    OpenShiftError,
)

# Default timeout for oc commands (seconds)
OC_DEFAULT_TIMEOUT = 30
# Timeout for oc exec operations (may be slow after pod restart)
OC_EXEC_TIMEOUT = 120
# Timeout for rsync operations (5 minutes - large workspaces take time)
RSYNC_TIMEOUT = 300
# Number of retries for rsync on timeout
RSYNC_MAX_RETRIES = 3


class OcClient:
    """Wrapper for oc CLI commands.

    This class encapsulates all direct interactions with the oc CLI,
    providing a clean interface for running commands with proper
    timeout handling and error translation.
    """

    def __init__(self, config: OpenShiftConfig) -> None:
        """Initialize the OcClient.

        Args:
            config: OpenShift configuration with context and namespace.
        """
        self._config = config

    def run(
        self,
        *args: str,
        capture: bool = True,
        check: bool = True,
        input_data: str | None = None,
        timeout: int | None = None,
        namespace: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run an oc command.

        Args:
            *args: Command arguments (without 'oc').
            capture: Capture output (default True).
            check: Raise on non-zero exit (default True).
            input_data: Optional input to pass to stdin.
            timeout: Timeout in seconds (default OC_DEFAULT_TIMEOUT).
                     Use None to inherit class default, 0 for no timeout.
            namespace: Optional namespace to pass via -n flag. This is inserted
                      as a global oc flag before the subcommand, not passed to
                      the subcommand itself (important for rsync where -n means
                      dry-run to rsync, not namespace).

        Returns:
            CompletedProcess result.

        Raises:
            OcNotInstalledError: If oc is not installed.
            OcTimeoutError: If command times out.
            OpenShiftError: If command fails and check=True.
        """
        cmd = ["oc"]

        # Add context if specified
        if self._config.context:
            cmd.extend(["--context", self._config.context])

        # Add namespace as global flag (before subcommand)
        if namespace:
            cmd.extend(["-n", namespace])

        cmd.extend(args)

        # Determine timeout value
        if timeout is None:
            timeout_value: float | None = OC_DEFAULT_TIMEOUT
        elif timeout == 0:
            timeout_value = None  # No timeout
        else:
            timeout_value = timeout

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                input=input_data,
                timeout=timeout_value,
            )
        except subprocess.TimeoutExpired:
            cmd_str = " ".join(cmd)
            raise OcTimeoutError(
                f"oc command timed out after {timeout_value}s: {cmd_str}\n"
                "This may indicate network issues connecting to the cluster.\n"
                "Check your cluster connectivity and try: oc status"
            ) from None
        except FileNotFoundError as e:
            raise OcNotInstalledError(
                "oc CLI not found. Install it from your OpenShift cluster or "
                "run: brew install openshift-cli"
            ) from e

        if check and result.returncode != 0:
            stderr = result.stderr if capture else ""
            if "error: You must be logged in" in stderr:
                raise OcNotLoggedInError(
                    "Not logged in to OpenShift. Run: oc login <cluster-url>"
                )
            raise OpenShiftError(f"oc command failed: {stderr}")

        return result

    def check_connection(self) -> bool:
        """Check if logged in to OpenShift.

        Returns:
            True if logged in.

        Raises:
            OcNotLoggedInError: If not logged in.
        """
        result = self.run("whoami", check=False)
        if result.returncode != 0:
            raise OcNotLoggedInError(
                "Not logged in to OpenShift. Run: oc login <cluster-url>"
            )
        return True

    def get_current_namespace(self) -> str:
        """Get the current namespace from oc config.

        Returns:
            Current namespace name.
        """
        result = self.run(
            "config", "view", "--minify", "-o",
            "jsonpath={.contexts[0].context.namespace}"
        )
        ns = result.stdout.strip()
        return ns if ns else "default"

    def verify_namespace(self, namespace: str) -> None:
        """Verify the target namespace exists.

        Args:
            namespace: Namespace name to verify.

        Raises:
            NamespaceNotFoundError: If the namespace does not exist.
        """
        result = self.run("get", "namespace", namespace, check=False)
        if result.returncode != 0:
            raise NamespaceNotFoundError(
                f"Namespace '{namespace}' does not exist. "
                f"Please create it or switch to an existing namespace."
            )
