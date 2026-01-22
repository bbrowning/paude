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

1. **Update Makefile**: Modify `make test-python` to always set PYTHONPATH to the local source:
   ```makefile
   test-python:
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

- `Makefile` (test-python target)
- `pyproject.toml` (pytest configuration)
- `containers/paude/Dockerfile` (if container build is involved)
