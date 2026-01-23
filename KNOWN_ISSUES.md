# Known Issues

Tracking known issues that need to be fixed. Each bug includes enough context for someone without prior knowledge to identify, reproduce, and solve the issue.

## BUG-001: Test environment uses stale installed package instead of local source

**Status**: Open
**Severity**: Medium (development friction)
**Discovered**: 2026-01-22 during native installer migration

### Summary

When running `make test` (or `pytest`) after modifying Python source files in `src/paude/`, the tests may use a stale pre-installed version of the package instead of the modified source code. This causes tests to pass or fail based on old code, not your changes.

### How to Reproduce

1. Make a change to a Python source file, e.g., edit `src/paude/config/dockerfile.py`
2. Run `make test` or `pytest tests`
3. Observe that tests still see the OLD version of the code

You can verify this is happening by checking the coverage report paths. If you see paths like `/opt/workspace-src/src/paude/...` instead of `src/paude/...`, the tests are using the installed package, not your local source.

### Root Cause

The issue occurs when:

1. The package was previously installed via `pip install -e .` into a virtual environment
2. The installed package location (e.g., `/opt/workspace-src`) is mounted read-only or is a separate copy
3. Python's import system finds the installed package before the local source

This is particularly problematic when developing inside paude's own container (dogfooding), where:
- The workspace is mounted at `/Volumes/SourceCode/paude`
- But there's also an installed copy at `/opt/workspace-src` from the container build
- The installed copy takes precedence in the Python path

### Workaround

Force Python to use the local source by prepending it to `PYTHONPATH`:

```bash
PYTHONPATH=/Volumes/SourceCode/paude/src:$PYTHONPATH pytest tests
```

Or more generally:

```bash
PYTHONPATH=$(pwd)/src:$PYTHONPATH pytest tests
```

### Proposed Fix Options

1. **Update Makefile**: Modify `make test` to always set PYTHONPATH to the local source:
   ```makefile
   test:
       PYTHONPATH=$(PWD)/src:$$PYTHONPATH pytest --cov=paude --cov-report=term-missing
   ```

2. **Use pytest configuration**: Add to `pyproject.toml`:
   ```toml
   [tool.pytest.ini_options]
   pythonpath = ["src"]
   ```

3. **Uninstall conflicting package**: Before testing, ensure no installed version conflicts:
   ```bash
   pip uninstall paude -y 2>/dev/null || true
   ```

4. **Container build fix**: Don't pre-install paude in the development container, or install it in a way that doesn't conflict with volume mounts.

### Acceptance Criteria for Fix

- [ ] Running `make test` uses local source files, not any installed version
- [ ] Changes to `src/paude/*.py` are immediately reflected in test runs without manual steps
- [ ] Coverage reports show paths starting with `src/paude/`, not `/opt/workspace-src/`
- [ ] Works both inside and outside the paude container
- [ ] Documented in CONTRIBUTING.md if any manual steps are still needed

### Related Files

- `Makefile` (test target)
- `pyproject.toml` (pytest configuration)
- `containers/paude/Dockerfile` (if container build is involved)

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
