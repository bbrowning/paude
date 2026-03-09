# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-002: cli.py (1,918 lines)

**Status**: Partially Complete
**Priority**: High (every new command adds complexity)
**Discovered**: 2026-01-29 during code quality analysis
**Partial completion**: 2026-03-08 — Extracted `_get_backend_instance()` and `_auto_select_session()` helpers

**Problem:** Commands implement logic instead of delegating. Backend detection repeated in every command.

**Completed:**
- `_get_backend_instance()` consolidates PodmanBackend/OpenShiftConfig+OpenShiftBackend creation (was repeated 6+ times)
- `_auto_select_session()` consolidates workspace-match → all-sessions → 0/1/multi logic (was repeated 4 times)
- `_resolve_backend_for_domains()` simplified to use `_get_backend_instance()`
- `session_delete`, `session_start`, `session_stop`, `session_connect`, `session_cp` refactored to use helpers

**Remaining:**
- File still 1,918 lines — needs splitting into a package (cli/ directory)
- Move image building orchestration to dedicated module
- Each command should be < 50 lines, delegating to helpers

### REFACTOR-004: Extract Duplicated Utilities

**Status**: Partially Complete
**Priority**: Medium
**Discovered**: 2026-01-29 during code quality analysis
**Partial completion**: 2026-03-07 — `_encode_path()`/`_decode_path()` extracted to `backends/shared.py`

**Completed:**
- `_encode_path()` / `_decode_path()` extracted to `backends/shared.py` with `url_safe` parameter

**Remaining:**
- `_generate_session_name()` remains duplicated (intentionally different implementations: Podman uses `secrets.token_hex`, OpenShift uses `hashlib.sha256`)

## Security Hardening Backlog

Deferred items from the network egress security audit (2026-03-06).

### SEC-001: GitHub API allows POST/PUT through proxy

**Status**: Open (by design)
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

GitHub's GraphQL API uses POST for ALL operations, including reads (`gh pr list`, `gh issue list`). Blocking POST/PUT at the proxy level would break read-only `gh` CLI usage. The correct mitigation is using a read-only Personal Access Token (PAT) rather than proxy-level HTTP method filtering.

### SEC-002: K8s service account token auto-mounted

**Status**: Open
**Severity**: Medium
**Discovered**: 2026-03-06 during network egress security audit

Kubernetes auto-mounts a service account token into every pod. This token could be used to interact with the K8s API if the container process is compromised. Needs testing with `automountServiceAccountToken: false` in the pod spec.

### SEC-003: K8s service environment variables leak cluster info

**Status**: Open
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

Kubernetes injects environment variables for every service in the namespace (e.g., `KUBERNETES_SERVICE_HOST`, `KUBERNETES_SERVICE_PORT`). These leak internal cluster information. Needs testing with `enableServiceLinks: false` in the pod spec.

### SEC-004: DNS tunneling via cluster DNS

**Status**: Open (out of scope)
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

Cluster DNS could theoretically be used for DNS tunneling to exfiltrate data. This is a cluster-level concern and out of paude's scope — requires cluster-level DNS policies or external DNS filtering.

### SEC-005: `no_proxy` not set for internal services

**Status**: Open
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

The `no_proxy` environment variable is not explicitly set, which could allow processes to bypass the proxy for internal cluster services. Needs analysis of which internal endpoints should be accessible and whether proxy bypass is a concern.
