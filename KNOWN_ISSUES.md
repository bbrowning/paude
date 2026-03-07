# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## BUG-003: Multi-pod git sync conflicts when syncing .git directory

**Status**: Open
**Severity**: Medium (data loss risk if user syncs incorrectly)
**Discovered**: 2026-01-23 during OpenShift sync design discussion

### Summary

When multiple OpenShift pods are running against the same local codebase and each makes independent git commits, syncing the `.git` directory from one pod will overwrite the commit history from other pods. This can result in lost work if the user isn't careful about sync order.

### Scenario

User has local repo at commit X and starts two remote Claude sessions:

```
Local:  main @ commit X
Pod A:  X → A1 → A2  (Claude added a feature)
Pod B:  X → B1 → B2  (Claude fixed a bug)
```

If user syncs from Pod A first:
```bash
paude sync pod-a --direction local
# Local now has: X → A1 → A2
```

Then syncs from Pod B:
```bash
paude sync pod-b --direction local
# Local now has: X → B1 → B2
# Commits A1 and A2 are LOST (overwritten)
```

### Root Cause

The `oc rsync` mechanism does a file-level sync of the entire workspace, including `.git/`. Since git history is stored in `.git/objects/`, syncing from Pod B replaces Pod A's objects. This is fundamentally a git branching problem manifesting as a sync problem.

### Current Behavior

- `.git` is intentionally NOT excluded from sync (so commits transfer)
- No warning is shown when syncing to a directory with uncommitted/unpushed changes
- No branch isolation between sessions

### Workarounds

**Option 1: Each pod works on a unique branch (recommended)**
```bash
# When starting each session, have Claude create a unique branch
# In pod A: git checkout -b claude/feature-pod-a
# In pod B: git checkout -b claude/bugfix-pod-b

# Sync both back - no conflict since different branches
paude sync pod-a --direction local
paude sync pod-b --direction local

# Locally merge as desired
git merge claude/feature-pod-a
git merge claude/bugfix-pod-b
```

**Option 2: Exclude .git from sync, reconstruct commits locally**
```bash
# Manually add .git to exclude patterns
# Sync files only, create commits locally based on diffs
# Con: Lose Claude's commit messages and granular history
```

**Option 3: Export patches from each pod before sync**
```bash
# In each pod before sync:
git format-patch origin/main -o /tmp/patches

# Sync patches separately, apply locally in desired order
git am /tmp/patches/*.patch
```

**Option 4: Sequential sync with push/pull coordination**
```bash
# Sync pod A, push to remote
paude sync pod-a --direction local
git push origin main

# Connect to pod B, pull updated main, rebase its work
oc exec -it pod-b -- git pull --rebase origin main

# Then sync pod B
paude sync pod-b --direction local
```

### Proposed Fix Options

1. **Branch-per-session feature**: Add `--branch` flag to session creation that auto-creates a unique branch:
   ```bash
   paude create my-feature --branch claude/my-feature-$(date +%s)
   ```
   This makes multi-pod workflows safer by default.

2. **Pre-sync safety check**: Before syncing `--direction local`, warn if:
   - Local has unpushed commits that would be overwritten
   - Local has uncommitted changes
   - Another session was more recently synced (detect via marker file)

3. **Sync strategy flag**: Add `--git-strategy` option:
   - `--git-strategy=overwrite` (current behavior)
   - `--git-strategy=merge` (attempt git merge after sync)
   - `--git-strategy=branch` (sync to a new branch)
   - `--git-strategy=exclude` (exclude .git from sync)

4. **Session sync manifest**: Track which sessions have synced and when, warn about conflicts:
   ```
   ~/.paude/sync-manifest.json
   {
     "/path/to/repo": {
       "last_sync": "pod-a",
       "last_sync_time": "2026-01-23T10:00:00Z",
       "active_sessions": ["pod-a", "pod-b"]
     }
   }
   ```

### Acceptance Criteria for Fix

- [ ] User is warned before sync would overwrite unpushed local commits
- [ ] Multi-pod workflows have a safe default (branch isolation or warnings)
- [ ] Documentation explains multi-pod git workflow best practices
- [ ] No data loss when user follows documented workflow

### Related Files

- `src/paude/backends/openshift/backend.py` (session lifecycle methods)
- `src/paude/backends/openshift/sync.py` (`sync_credentials`, `sync_full_config` methods)
- `src/paude/cli.py` (`session sync` command)

## ENHANCEMENT-001: DevSpace sync as alternative to oc rsync

**Status**: Open (research complete, not implemented)
**Priority**: Low (oc rsync works, DevSpace adds complexity)
**Discovered**: 2026-01-22 during OpenShift backend research

### Summary

The OpenShift backend research evaluated DevSpace sync as a more sophisticated alternative to `oc rsync`. DevSpace offers bidirectional real-time sync with file watching, which could benefit users who want automatic sync rather than explicit sync commands.

### Current State

- Research completed in `docs/features/2026-01-22-openshift-backend/RESEARCH.md`
- Decision was to use `oc rsync` for MVP (simpler, no external dependency)
- DevSpace noted as potential future enhancement

### DevSpace Advantages

- Bidirectional sync with conflict detection
- File watching (changes sync automatically)
- No special container privileges required
- CNCF project, actively maintained (v6.3.18 as of Sep 2025)
- Works with any container that has `tar` command

### DevSpace Disadvantages

- External binary dependency (user must install DevSpace)
- More complex setup and troubleshooting
- Real-time sync may conflict with explicit sync model preferred by some users
- Overkill for users who prefer manual sync control

### When to Consider Implementing

- If users request real-time sync as a feature
- If `oc rsync` proves unreliable in practice
- If multi-pod conflict issues (BUG-003) become common and DevSpace's conflict detection helps

### Implementation Notes

```bash
# DevSpace sync can be used standalone without full DevSpace workflow
devspace sync --local-path=./src --container-path=/workspace \
  --pod=paude-session-0 --namespace=paude

# Or integrate sync component directly
```

### Related Files

- `docs/features/2026-01-22-openshift-backend/RESEARCH.md` (detailed comparison)
- `src/paude/backends/openshift/sync.py` (would need new sync implementation)

## BUG-004: Claude Code reports "not a git repository" on OpenShift session start

**Status**: Fixed
**Severity**: Medium (breaks worktree isolation and git-dependent features)
**Discovered**: 2026-03-07 during tech debt analysis
**Fixed**: 2026-03-07

### Summary

When using `paude create --git` with the OpenShift backend, Claude Code reports "not a git repository" because of a race condition: the entrypoint launches Claude before git push completes.

### Root Cause (Confirmed)

Race condition in OpenShift `create_session` flow. The pod's entrypoint launches Claude immediately on pod start, but `_setup_git_after_create()` (which does `git push`) runs after the pod is already running:

1. Create StatefulSet with entrypoint that launches Claude
2. Wait for pod ready — pod is running, entrypoint launches Claude
3. Sync credentials to pod — Claude is already starting up
4. Return to CLI
5. `_setup_git_after_create()` — git push happens HERE, after Claude is running

Claude Code captures git metadata once at conversation init, so by the time `.git` exists, Claude has already cached "not a git repo".

Podman is NOT affected because it uses `sleep infinity` as entrypoint — Claude only launches on `paude connect`, which is always after git setup.

### Fix

Added `PAUDE_WAIT_FOR_GIT` environment variable mechanism:
- `cli.py`: Sets `PAUDE_WAIT_FOR_GIT=1` in session env when `--git` is used with OpenShift
- `entrypoint-session.sh`: `wait_for_git()` function waits for `/pvc/workspace/.git` to exist before launching Claude (120s timeout, graceful fallback)

### Related Files

- `src/paude/cli.py` (env var injection)
- `containers/paude/entrypoint-session.sh` (`wait_for_git` function)

## Dead Code Backlog

Items identified during dead code scan (2026-03-07). Not removed because they have tests or are part of the public API.

### DEAD-001: utils.py functions unused in source code

**Status**: Open
**Priority**: Low
**Discovered**: 2026-03-07 during dead code scan

`check_requirements()`, `check_git_safety()`, `RequirementError`, and `resolve_path()` in `src/paude/utils.py` are never imported from any source file in `src/paude/`. They are only referenced in `tests/test_utils.py`. Additionally, `resolve_path()` is duplicated in `src/paude/mounts.py` (which uses its own copy).

### DEAD-002: ContainerRunner.run_claude() unused in source code

**Status**: Open
**Priority**: Low
**Discovered**: 2026-03-07 during dead code scan

`run_claude()` in `src/paude/container/runner.py` is never called from any source file. It appears to be a legacy method from before the session-based architecture. It has test coverage in `tests/test_container.py`.

### DEAD-003: PodmanBackend.run_proxy() and run_post_create() unused in source code

**Status**: Open
**Priority**: Low
**Discovered**: 2026-03-07 during dead code scan

`run_proxy()` and `run_post_create()` wrapper methods in `src/paude/backends/podman.py` are never called from `cli.py` or any other source file. They delegate to `ContainerRunner` methods and have test coverage in `tests/test_backends.py`.

### DEAD-004: VolumeNotFoundError and PodNotFoundError never raised

**Status**: Open
**Priority**: Low
**Discovered**: 2026-03-07 during dead code scan

`VolumeNotFoundError` (in `src/paude/container/volume.py`) and `PodNotFoundError` (in `src/paude/backends/openshift/exceptions.py`) are defined and re-exported via `__init__.py` / `__all__` but never raised anywhere in the codebase.
## Refactoring Backlog

Technical debt identified during codebase analysis. Address these before adding significant new functionality to affected files.

### REFACTOR-002: cli.py (1,601 lines)

**Status**: Open
**Priority**: High (every new command adds complexity)
**Discovered**: 2026-01-29 during code quality analysis

**Problem:** Commands implement logic instead of delegating. Backend detection repeated in every command.

**Recommended changes:**
- Extract `find_session_backend()` to shared function
- Move image building orchestration to dedicated module
- Each command should be < 50 lines, delegating to helpers

### REFACTOR-003: container/image.py (708 lines)

**Status**: Open
**Priority**: Medium
**Discovered**: 2026-01-29 during code quality analysis

**Problem:** `prepare_build_context` is 319 lines mixing local and remote build logic.

**Recommended split:**
- Separate `BuildContextBuilder` class
- Split remote vs local build paths

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
