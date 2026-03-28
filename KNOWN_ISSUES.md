# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-002: cli.py monolith

**Status**: Resolved
**Priority**: High (every new command adds complexity)
**Discovered**: 2026-01-29 during code quality analysis
**Resolved**: 2026-03-09 — Split 2,246-line `cli.py` into `cli/` package with 8 modules (app.py, help.py, helpers.py, create.py, commands.py, remote.py, domains.py, status.py). Backward compatibility preserved via `__init__.py` re-exports. Dead `_encode_path`/`_decode_path` wrappers removed from `podman.py`.

### REFACTOR-003: Oversized files, methods, and classes

**Status**: Open
**Priority**: Medium (address before adding significant new functionality to affected files)
**Discovered**: 2026-03-24 during v0.13.0 pre-release audit

**Files exceeding 400-line limit:**
- `backends/openshift/sync.py` — 595 lines
- `cli/commands.py` — 580 lines
- `backends/podman/backend.py` — 504 lines
- `backends/openshift/proxy.py` — 484 lines
- `workflow.py` — 467 lines
- `backends/openshift/build.py` — 459 lines

**Methods exceeding 50-line limit:**
- `workflow.py` — `harvest_session()` (~102 lines), `status_sessions()` (~84), `reset_session()` (~72)
- `cli/commands.py` — `session_cp()` (~75 lines)
- `backends/openshift/sync.py` — `_sync_agent_config()` (~90 lines)
- `backends/podman/backend.py` — `create_session()` (~95 lines)

**Classes exceeding 20-method limit:**
- `PodmanBackend` in `backends/podman/backend.py` — 26 methods
- `OpenShiftBackend` in `backends/openshift/backend.py` — 28 methods

### REFACTOR-004: Duplicated Dockerfile between static file and generated code

**Status**: Open
**Priority**: Medium (every new container script requires updates in multiple places)
**Discovered**: 2026-03-28 while adding entrypoint-lib-openclaw.sh

The static `containers/paude/Dockerfile` and the programmatic Dockerfile generator in `src/paude/config/dockerfile.py` must be kept in sync manually. Adding a new script to the container requires changes in four places:

1. `containers/paude/Dockerfile` — static COPY line (used by local Podman/Docker builds)
2. `src/paude/config/dockerfile.py` — generated COPY line (used by OpenShift builds)
3. `src/paude/container/build_context.py` — `copy_entrypoints()` file list
4. `pyproject.toml` — `force-include` for production wheel packaging

This caused a bug where the new OpenClaw helper script was added to the static Dockerfile but not to the generated one, so OpenShift builds silently omitted it. Consider having a single source of truth for the list of container scripts (e.g., a constant in `build_context.py`) that all four locations reference, or generating the static Dockerfile from the same code path.

## Agent Limitations

Issues caused by upstream agent behavior, not paude bugs.

### AGENT-001: Gemini CLI token expiry in long-running sessions

**Status**: Open (upstream limitation)
**Severity**: Low
**Discovered**: 2026-03-11 during Gemini CLI idle session testing

When a Gemini CLI session sits idle inside a paude container (Podman or OpenShift) for ~1 hour, the OAuth access token expires. The already-running Gemini process does not gracefully refresh the token and instead prompts for browser-based re-authentication, which is not possible inside a container.

The container has everything needed to refresh tokens (oauth_creds.json with a valid refresh_token, network access to oauth2.googleapis.com). Starting a fresh `gemini` process inside the container works fine and refreshes the token automatically. The issue is specific to the long-running process not handling token expiry during idle periods.

**Workaround**: Kill the existing Gemini process and restart it inside the tmux session. The new process will pick up the refresh token and authenticate successfully.

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

**Status**: Resolved
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit
**Resolved**: 2026-03-11 — Added `NO_PROXY=localhost,127.0.0.1` and `no_proxy=localhost,127.0.0.1` to both Podman and OpenShift proxy environments. This prevents internal localhost requests (e.g., Cursor agent's `GET http://localhost/getRepositoryInfo`) from being routed through the proxy and blocked.
