# Python venv Isolation - Implementation Tasks

## Phase 1: Core Detection and Shadowing

### Task 1.1: Add venv detection utility

**File**: `src/paude/venv.py` (new file)

**Implementation**:
```python
from pathlib import Path

COMMON_VENV_NAMES = [".venv", "venv", ".virtualenv", "env", ".env"]

def is_venv(path: Path) -> bool:
    """Check if directory is a Python virtual environment."""
    return path.is_dir() and (path / "pyvenv.cfg").is_file()

def find_venvs(workspace: Path) -> list[Path]:
    """Find venv directories in workspace (non-recursive)."""
    venvs = []
    for name in COMMON_VENV_NAMES:
        candidate = workspace / name
        if is_venv(candidate):
            venvs.append(candidate)
    return venvs
```

**Acceptance criteria**:
- [ ] Detects `.venv` with pyvenv.cfg
- [ ] Detects `venv` with pyvenv.cfg
- [ ] Does not detect directories without pyvenv.cfg
- [ ] Does not detect files named `.venv`
- [ ] Only checks common names (no full directory scan)

**Tests**: `tests/test_venv.py`

---

### Task 1.2: Update PaudeConfig model

**File**: `src/paude/config/models.py`

**Changes**:
```python
from typing import Literal

# Add type alias
VenvMode = Literal["auto", "none"] | list[str]

@dataclass
class PaudeConfig:
    # ... existing fields ...

    # venv isolation mode
    venv: VenvMode = "auto"
```

**Acceptance criteria**:
- [ ] Default value is "auto"
- [ ] Accepts "none" string
- [ ] Accepts list of directory names

**Tests**: `tests/test_config.py`

---

### Task 1.3: Add venv mounts to mount builder

**File**: `src/paude/mounts.py`

**Changes**:
- Add new function `build_venv_mounts(workspace, venv_mode)`
- Returns list of `--mount type=tmpfs,destination=...` arguments
- Integrate with existing `build_mounts()` or create separate function

```python
def build_venv_mounts(
    workspace: Path,
    venv_mode: VenvMode
) -> list[str]:
    """Build tmpfs mounts to shadow venv directories.

    Args:
        workspace: Path to workspace directory.
        venv_mode: "auto", "none", or list of directory names.

    Returns:
        List of mount arguments for podman.
    """
    if venv_mode == "none":
        return []

    if venv_mode == "auto":
        venvs = find_venvs(workspace)
    else:  # list of names
        venvs = [workspace / name for name in venv_mode]
        venvs = [v for v in venvs if v.exists()]

    mounts = []
    for venv_path in venvs:
        resolved = resolve_path(venv_path)
        if resolved:
            mounts.extend([
                "--mount", f"type=tmpfs,destination={resolved}"
            ])
    return mounts
```

**Acceptance criteria**:
- [ ] Returns empty list when mode is "none"
- [ ] Finds venvs automatically when mode is "auto"
- [ ] Uses specified directories when mode is list
- [ ] Handles non-existent directories gracefully
- [ ] Resolves symlinks in venv paths

**Tests**: `tests/test_mounts.py`

---

### Task 1.4: Integrate venv mounts into runner

**File**: `src/paude/container/runner.py`

**Changes**:
- Import `build_venv_mounts` from mounts module
- Call it with workspace and config.venv
- Append results to podman command after workspace mount

**Acceptance criteria**:
- [ ] Venv mounts appear after workspace mount (order matters!)
- [ ] Works with auto detection
- [ ] Prints message when shadowing venvs (for user visibility)

**Tests**: Integration test or manual verification

---

## Phase 2: Configuration Support

### Task 2.1: Parse venv from paude.json

**File**: `src/paude/config/parser.py`

**Changes to `_parse_paude_json()`**:
```python
def _parse_paude_json(config_file: Path, data: dict[str, Any]) -> PaudeConfig:
    # ... existing parsing ...

    # Parse venv mode
    venv_config = data.get("venv", "auto")
    if venv_config not in ("auto", "none") and not isinstance(venv_config, list):
        raise ConfigError(
            f"Invalid venv config: expected 'auto', 'none', or list of directories"
        )

    return PaudeConfig(
        # ... existing fields ...
        venv=venv_config,
    )
```

**Acceptance criteria**:
- [ ] Parses "auto" string
- [ ] Parses "none" string
- [ ] Parses list of strings
- [ ] Raises ConfigError for invalid values
- [ ] Defaults to "auto" when not specified

**Tests**: `tests/test_config.py`

---

### Task 2.2: Add user feedback for venv shadowing

**File**: `src/paude/mounts.py` or `src/paude/container/runner.py`

**Implementation**:
- Print message when venvs are detected and shadowed
- Example: `Shadowing venv directories: .venv, venv`
- Skip message if no venvs found

**Acceptance criteria**:
- [ ] Message printed to stderr
- [ ] Lists all shadowed directories
- [ ] No message when mode is "none"
- [ ] No message when no venvs found

---

### Task 2.3: Add --no-venv-shadow CLI flag (optional)

**File**: `src/paude/cli.py`

**Changes**:
- Add `--no-venv-shadow` flag
- When present, overrides config to venv="none"
- Useful for debugging

**Acceptance criteria**:
- [ ] Flag disables venv shadowing
- [ ] Works with any config file

---

## Phase 3: Documentation and Polish

### Task 3.1: Update README

**File**: `README.md`

**Add section**:
```markdown
## Python Virtual Environments

Paude automatically detects Python virtual environment directories (`.venv`,
`venv`, etc.) and shadows them with empty tmpfs mounts. This allows you to:

- Use your host venv on your Mac
- Create a separate container venv inside paude

### Automatic Setup

Add to your `paude.json`:

```json
{
  "setup": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

### Disabling venv Isolation

If you need to disable this behavior:

```json
{
  "venv": "none"
}
```
```

**Acceptance criteria**:
- [ ] Documents default behavior
- [ ] Shows setup example
- [ ] Shows how to disable

---

### Task 3.2: Add tests for all venv modes

**File**: `tests/test_venv.py`, `tests/test_mounts.py`

**Tests needed**:
- [ ] `is_venv()` correctly identifies venvs
- [ ] `is_venv()` rejects non-venvs
- [ ] `find_venvs()` finds all common names
- [ ] `find_venvs()` returns empty for clean directory
- [ ] `build_venv_mounts()` with auto mode
- [ ] `build_venv_mounts()` with none mode
- [ ] `build_venv_mounts()` with manual list
- [ ] Config parsing for all venv modes

---

### Task 3.3: Integration testing

**Manual test procedure**:
1. Create Python project with venv on host
2. Run `paude`
3. Verify `.venv` is empty inside container
4. Create new venv inside container
5. Verify host venv unchanged
6. Exit container
7. Verify host venv still works

---

## Summary Checklist

- [ ] **Phase 1**: Core detection and shadowing
  - [ ] venv detection utility
  - [ ] PaudeConfig model update
  - [ ] venv mount builder
  - [ ] Runner integration

- [ ] **Phase 2**: Configuration support
  - [ ] paude.json parsing
  - [ ] User feedback messages
  - [ ] CLI flag (optional)

- [ ] **Phase 3**: Documentation and polish
  - [ ] README updates
  - [ ] Comprehensive tests
  - [ ] Integration testing
