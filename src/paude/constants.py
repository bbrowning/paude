"""Shared constants for paude."""

CONTAINER_WORKSPACE = "/pvc/workspace"
CONTAINER_HOME = "/home/paude"
# Runtime user identity. Pinned so an image rebuild (e.g. `paude upgrade`) never
# drifts the UID out from under an existing /pvc volume, which would make the
# volume unwritable (EACCES). gid 0 (root) matches volumes created before the
# user was pinned and always exists. The static containers/paude/Dockerfile
# hardcodes these same values (it can't import Python) — keep them in sync.
CONTAINER_RUNTIME_UID = 1000
CONTAINER_RUNTIME_GID = 0
CONTAINER_GIT_CONFIG = "/pvc/.gitconfig"
CONTAINER_ENTRYPOINT = "/usr/local/bin/entrypoint.sh"
BASE_REF_NAME = "refs/paude/base"
GCP_ADC_FILENAME = "application_default_credentials.json"
GCP_ADC_SECRET_NAME = "paude-gcp-adc"  # noqa: S105
GCP_ADC_TARGET = f"{CONTAINER_HOME}/.config/gcloud/{GCP_ADC_FILENAME}"
SANDBOX_CONFIG_TARGET = f"{CONTAINER_HOME}/.paude/agent-sandbox-config.sh"
DEFAULT_BRANCHES = ("main", "master")
CLONE_FROM_ORIGIN_TIMEOUT = 600  # seconds (10 minutes)
