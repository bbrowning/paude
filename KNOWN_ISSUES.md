# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## BUG-002: Claude Code plugins not available in OpenShift backend

**Status**: Open
**Severity**: Low (plugins are optional, core functionality works)
**Discovered**: 2026-01-23 during OpenShift backend testing

### Summary

When using the OpenShift backend (`--backend=openshift`), Claude Code plugins from the host's `~/.claude/plugins/` directory are not available in the container. Claude reports that plugins failed to install.

### How to Reproduce

1. Have Claude Code plugins configured locally in `~/.claude/plugins/`
2. Run `paude --backend=openshift`
3. Observe Claude reporting plugin installation failures

### Root Cause

The OpenShift backend creates a Kubernetes Secret containing only core Claude config files:
- `settings.json`
- `credentials.json`
- `statsig.json`
- `claude.json`

The `~/.claude/plugins/` directory is not included because:
1. Plugins can contain large files that may exceed Kubernetes Secret size limits (1MB)
2. Plugins may contain binaries or executables
3. Plugin symlink structures may not transfer well via Secrets

### Workaround

Plugins must be installed manually inside the OpenShift container:

```bash
# Attach to the session
paude attach <session-id> --backend=openshift

# Install plugins manually inside the container
# (plugin installation commands depend on the specific plugin)
```

### Proposed Fix Options

1. **ConfigMap for plugins**: Use a ConfigMap instead of Secret for plugins (still has 1MB limit)

2. **PersistentVolume**: Mount plugins via a PersistentVolume that's populated separately

3. **Plugin download at runtime**: Have the entrypoint download/install plugins based on a list in settings.json

4. **Increase Secret limit**: Split plugins across multiple Secrets if needed

### Acceptance Criteria for Fix

- [ ] Plugins from host `~/.claude/plugins/` are available in OpenShift containers
- [ ] Plugin installation doesn't fail due to size limits
- [ ] Plugins work correctly with OpenShift's arbitrary UID

### Related Files

- `src/paude/backends/openshift.py` (`_create_claude_secret` method)
- `containers/paude/entrypoint.sh` (seed file copying)

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

- `src/paude/backends/openshift.py` (`sync_session`, `_rsync_from_pod` methods)
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
- `src/paude/backends/openshift.py` (would need new sync implementation)

## TECH-DEBT-001: OpenShift backend has duplicated legacy and new session methods

**Status**: Open
**Priority**: Medium (causes bugs when adding features, maintenance burden)
**Discovered**: 2026-01-23 during proxy deployment implementation

### Summary

The OpenShift backend (`src/paude/backends/openshift.py`) contains two parallel code paths for session management:

1. **New protocol methods**: `create_session()`, `start_session()`, `stop_session()`, `delete_session()` - uses StatefulSets
2. **Legacy methods**: `start_session_legacy()`, `stop_session_legacy()`, `attach_session_legacy()`, `list_sessions_legacy()` - uses ephemeral Pods

Both code paths need to implement the same features (proxy deployment, NetworkPolicy, credential mounting, etc.), leading to duplicated logic and bugs when one path is updated but not the other.

### Impact

When implementing the proxy pod deployment feature, the new `create_session()` method was updated to create proxy resources, but `start_session_legacy()` was initially missed. This caused legacy sessions to have a NetworkPolicy that referenced a non-existent proxy pod, completely breaking network access.

Every new feature touching session creation/management must be implemented twice, increasing:
- Development time (implement in two places)
- Bug surface area (easy to miss one path)
- Test burden (test both paths)
- Code size (800+ lines in openshift.py)

### Current State

The legacy methods exist because:
1. The original implementation used ephemeral Pods (no persistence between sessions)
2. The new StatefulSet-based approach was added later for persistent sessions
3. Both are still in use depending on CLI invocation path

### Proposed Fix

**Option 1: Migrate legacy to new protocol (recommended)**
- Update all CLI code paths to use the new session protocol
- Legacy methods become thin wrappers that call new methods
- Eventually deprecate and remove legacy methods

**Option 2: Extract shared logic**
- Create internal helper methods for proxy, NetworkPolicy, credentials
- Both legacy and new methods call these shared helpers
- Still have two entry points but shared implementation

**Option 3: Feature flags**
- Use feature flags to gradually migrate users to new protocol
- Remove legacy code once migration is complete

### Affected Code

```
src/paude/backends/openshift.py:
  - start_session_legacy()    (lines ~2140-2330)
  - stop_session_legacy()     (lines ~2350-2385)
  - attach_session_legacy()   (lines ~2420-2460)
  - list_sessions_legacy()    (lines ~2385-2420)
  - sync_workspace()          (lines ~2460-2520)

vs.

  - create_session()          (lines ~1625-1720)
  - start_session()           (lines ~1815-1860)
  - stop_session()            (lines ~1860-1895)
  - delete_session()          (lines ~1740-1815)
  - connect_session()         (lines ~1895-1940)
```

### Acceptance Criteria for Fix

- [ ] Single code path for session creation/management
- [ ] New features only need to be implemented once
- [ ] Legacy CLI invocations continue to work (backward compatibility)
- [ ] Test coverage consolidated to single path
- [ ] Code reduced by ~200-300 lines

### Related Files

- `src/paude/backends/openshift.py` (primary file with duplication)
- `src/paude/backends/base.py` (Backend protocol definition)
- `src/paude/cli.py` (CLI code that chooses which methods to call)
