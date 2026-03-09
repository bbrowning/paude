# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-002: cli.py monolith

**Status**: Resolved
**Priority**: High (every new command adds complexity)
**Discovered**: 2026-01-29 during code quality analysis
**Resolved**: 2026-03-09 — Split 2,246-line `cli.py` into `cli/` package with 8 modules (app.py, help.py, helpers.py, create.py, commands.py, remote.py, domains.py, status.py). Backward compatibility preserved via `__init__.py` re-exports. Dead `_encode_path`/`_decode_path` wrappers removed from `podman.py`.

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
