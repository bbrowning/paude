# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-003: Oversized files, methods, and classes

**Status**: Open
**Priority**: Medium (address before adding significant new functionality to affected files)
**Discovered**: 2026-03-24 during v0.13.0 pre-release audit

**Files exceeding 400-line limit:**
- `backends/openshift/sync.py` — dead code for OpenShift (kept for Podman base class)
- `cli/commands.py` — 580 lines
- `backends/podman/backend.py` — 504 lines
- `backends/openshift/proxy.py` — 484 lines
- `workflow.py` — 467 lines
- `backends/openshift/build.py` — 459 lines

**Methods exceeding 50-line limit:**
- `workflow.py` — `harvest_session()` (~102 lines), `status_sessions()` (~84), `reset_session()` (~72)
- `cli/commands.py` — `session_cp()` (~75 lines)
- `backends/openshift/sync.py` — dead code (see above)
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

## Architecture: GitOps Migration

Tracking the migration from imperative orchestration to declarative, GitOps-compatible session creation.

### ARCH-001: Imperative OpenShift session orchestration blocks GitOps workflows

**Status**: Open
**Priority**: Medium
**Discovered**: 2026-04-03 during architecture review

`paude create` orchestrates OpenShift sessions imperatively: Python code builds K8s resource dicts, pipes them as JSON to `oc apply -f -`, waits for pods, then runs `oc exec`/`oc cp`/`oc rsync` to inject config into running containers. This prevents GitOps workflows where manifests are checked into git and applied by ArgoCD or similar tools.

**Current imperative flow creates ~10 K8s resources per session:**

Declarative resources (already applied as JSON via `oc apply`):
- 2 Secrets (CA cert, proxy credentials) — `backends/openshift/certs.py`
- 1 headless Service (StatefulSet DNS) — `backends/openshift/session_lifecycle.py`
- 1 Deployment (proxy sidecar) — `backends/openshift/proxy.py`
- 1 Service (proxy ClusterIP) — `backends/openshift/proxy.py`
- 3 NetworkPolicies (agent egress, proxy egress, proxy ingress) — `backends/openshift/proxy.py`
- 1 StatefulSet with PVC template (agent pod, runs `tini -- sleep infinity`) — `backends/openshift/resources.py`

Post-apply imperative steps (resolved — config now mounted via ConfigMap):
- Poll `oc get pod` for readiness (still present, but standard K8s usage)

Build resources (shared, coupled to create):
- BuildConfig + ImageStream — `backends/openshift/build.py` (binary build from local dir)

**Six gaps block GitOps adoption:**

**Gap 1 — No manifest export layer.** Each resource builder calls `oc apply -f -` inline. There is no way to collect all resource specs and write them to disk as YAML. Fix: add a `ManifestCollector` that accumulates resource dicts and can either apply them or write to a directory. Resource builders return dicts instead of applying directly.

**Gap 2 — Config injected into running pods via `oc cp`/`oc exec`.** (Resolved) All config files (stub GCP ADC, gitconfig user.name/email, sandbox config script) are now packaged into a ConfigMap and mounted at `/credentials` before the container starts. No `oc cp`/`oc exec` is needed. Cursor auth and global gitignore syncing were removed entirely.

**Gap 3 — Secrets created inline during `paude create`.** CA cert is generated via openssl and credentials are gathered from the host environment, both stored as K8s Secrets during `paude create`. Fix: users pre-create secrets out-of-band (`oc create secret`, sealed-secrets, ESO, vault) and pass names via `--ca-secret` / `--creds-secret` flags. CA generation becomes a helper command (`paude setup-proxy-ca`). Paude manifests just reference secret names, never contain secret data.

**Gap 4 — Image builds coupled to session creation.** `build.py` creates BuildConfig/ImageStream and runs `oc start-build --from-dir=...` which uploads local files. Fix: separate `paude build` from `paude create`. Emitted YAML references a pre-built image by tag or digest.

**Gap 5 — Container starts with `sleep infinity`, agent launched via `oc exec`.** (Resolved) The StatefulSet command is now `tini -- entrypoint-session.sh` with `PAUDE_HEADLESS=1`. Config is pre-mounted via ConfigMap (Gap 2), so the entrypoint runs directly. No `sleep infinity` + `oc exec` pattern.

**Gap 6 — Interactive operations (`oc exec`, `oc port-forward`, connect).** No fix needed. These are operational commands that work against running resources. They are orthogonal to GitOps — declarative manages the desired state, interactive commands are for human access.

**Phased migration plan:**

Phase 1 — Manifest collection layer (low effort, high value):
- Add `ManifestCollector` class to accumulate resource dicts
- Resource builders return dicts instead of calling `oc apply` directly
- Add `--emit-yaml <dir>` flag to `paude create` that writes YAML files
- Files: `session_lifecycle.py`, `proxy.py`, `certs.py`, new `manifest.py`

Phase 2 — Decouple image build (low effort, medium value):
- Expose `paude build` as a standalone command
- `--emit-yaml` requires `--image` (no builds during YAML generation)
- Exclude BuildConfig/ImageStream from session manifests

Phase 3 — Externalize secrets (low-medium effort, high value):
- Add `--ca-secret` / `--creds-secret` flags to reference pre-created secrets
- Add `paude setup-proxy-ca` helper to create CA cert secret out-of-band
- Paude manifests reference secret names, never generate secret data inline
- Existing inline secret creation remains as default for backward compatibility

Phase 4 — Config as mounted volumes (done for OpenShift):
- Agent config sync and plugin path rewriting already removed (done)
- Remaining config (stub ADC, gitconfig user.name/email, sandbox script) packaged as ConfigMap (done)
- `sleep infinity` + `oc exec` pattern removed; entrypoint runs directly (done)
- ConfigMap includes `.ready` marker so entrypoint skips wait (done)
- Cursor auth and global gitignore syncing removed (done)
- Podman backend still uses old `podman cp`/`podman exec` pattern (future work)

## Security Hardening Backlog

Deferred items from the network egress security audit (2026-03-06).

### SEC-001: GitHub API allows POST/PUT through proxy

**Status**: Open (by design)
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

GitHub's GraphQL API uses POST for ALL operations, including reads (`gh pr list`, `gh issue list`). Blocking POST/PUT at the proxy level would break read-only `gh` CLI usage. The correct mitigation is using a read-only Personal Access Token (PAT) rather than proxy-level HTTP method filtering.

### SEC-004: DNS tunneling via cluster DNS

**Status**: Open (out of scope)
**Severity**: Low
**Discovered**: 2026-03-06 during network egress security audit

Cluster DNS could theoretically be used for DNS tunneling to exfiltrate data. This is a cluster-level concern and out of paude's scope — requires cluster-level DNS policies or external DNS filtering.

