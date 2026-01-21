# Python venv Isolation - Design Plan

## Goal

Enable Python development where:
- Host venv works on host machine
- Container creates separate venv
- Source code is shared between both
- No manual configuration required (auto-detection)

## Design Decisions

### 1. Auto-detection by Default

**Decision**: Scan workspace for venv directories and automatically shadow them.

**Rationale**:
- Most projects use `.venv` or `venv` naming
- `pyvenv.cfg` is a reliable marker file
- Zero configuration for common cases
- Can be disabled via config if needed

**Detection algorithm**:
```python
def is_venv(path: Path) -> bool:
    """Check if directory is a Python virtual environment."""
    return (path / "pyvenv.cfg").is_file()
```

### 2. Shadow with tmpfs

**Decision**: Use tmpfs mounts to shadow detected venv directories.

**Rationale**:
- Container gets empty directory, can create its own venv
- No disk space on host used for container venv
- Fast I/O (in-memory)
- Ephemeral by design (venv recreated each session)

**Implementation**:
```bash
# After workspace mount, shadow venv directories
podman run \
  -v /project:/project:rw \
  --mount type=tmpfs,destination=/project/.venv \
  ...
```

### 3. Configuration Options

**Decision**: Support three modes via `paude.json`:

```json
{
  "venv": "auto"           // Default: auto-detect and shadow
}

{
  "venv": "none"           // Disable: share venvs (broken, but user's choice)
}

{
  "venv": [".venv", "env"] // Manual: specific directories to shadow
}
```

**Rationale**:
- `auto` covers 95% of cases
- `none` for edge cases (maybe user wants to see errors)
- Manual list for non-standard venv names

### 4. devcontainer.json Support

**Decision**: Not supported in devcontainer.json (paude-specific feature).

**Rationale**:
- devcontainer spec doesn't have this concept
- Keep devcontainer.json portable
- Use paude.json for paude-specific features

### 5. Container-side venv Setup

**Decision**: Document workflow, don't automate venv creation.

**Rationale**:
- Users may use different tools (pip, poetry, uv, pipenv)
- postCreateCommand already exists for setup automation
- Keep paude focused on container orchestration, not Python tooling

**Recommended workflow**:
```json
{
  "postCreateCommand": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

Or with uv:
```json
{
  "postCreateCommand": "uv venv && uv pip install -r requirements.txt"
}
```

### 6. Edge Cases

**Empty venv directory on host**:
- If host has no venv yet, no directory to detect
- User creates venv on host later
- Container already has tmpfs at that path = works

**Multiple venvs**:
- Auto-detect finds all of them
- Each gets its own tmpfs shadow

**Nested projects**:
- Only scan top-level of workspace
- Don't recurse into subdirectories (too slow, rare case)

## Security Considerations

- venv shadowing is purely additive (extra mounts)
- Does not expose any new host paths
- tmpfs is isolated per container run
- No secrets or credentials involved

## User Experience

### Before (broken)
```
$ paude  # enters container
$ python -m pytest
/project/.venv/bin/python: bad interpreter: No such file or directory
```

### After (works)
```
$ paude  # enters container, .venv is empty tmpfs
$ python -m venv .venv  # creates container-local venv
$ .venv/bin/pip install -r requirements.txt
$ python -m pytest  # works!
```

### With postCreateCommand (automatic)
```json
// paude.json
{
  "setup": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

```
$ paude  # enters container, venv auto-created
$ python -m pytest  # just works!
```

## Implementation Phases

### Phase 1: Core Detection and Shadowing
- Add venv detection to workspace scanning
- Add tmpfs mounts for detected venvs
- Add `venv` field to PaudeConfig

### Phase 2: Configuration Support
- Parse `venv` from paude.json
- Support "auto", "none", and list modes
- Add validation and helpful warnings

### Phase 3: Documentation and Polish
- Update README with venv workflow
- Add examples to help output
- Add tests for all modes
