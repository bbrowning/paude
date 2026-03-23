"""Shared constants for paude."""

CONTAINER_WORKSPACE = "/pvc/workspace"
CONTAINER_HOME = "/home/paude"
CONTAINER_ENTRYPOINT = "/usr/local/bin/entrypoint.sh"
BASE_REF_NAME = "refs/paude/base"
GCP_ADC_FILENAME = "application_default_credentials.json"
GCP_ADC_SECRET_NAME = "paude-gcp-adc"  # noqa: S105
GCP_ADC_TARGET = f"{CONTAINER_HOME}/.config/gcloud/{GCP_ADC_FILENAME}"
DEFAULT_BRANCHES = ("main", "master")
CLONE_FROM_ORIGIN_TIMEOUT = 600  # seconds (10 minutes)
