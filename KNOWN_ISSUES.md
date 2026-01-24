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
