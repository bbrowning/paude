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
- `containers/paude/entrypoint-tmux.sh` (seed file copying)
