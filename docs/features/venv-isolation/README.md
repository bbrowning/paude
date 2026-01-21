# Python venv Isolation Feature

## Overview

This feature enables Python development where both host and container can have separate, functional virtual environments for the same project.

## The Problem

Python venvs contain absolute paths to the host Python interpreter. When a workspace with a venv is mounted into a container:
- The venv's `bin/python` symlink points to a non-existent host path
- Shebang lines in installed scripts reference host paths
- Binary extensions are compiled for the wrong platform

## The Solution

Paude automatically detects venv directories and "shadows" them with empty tmpfs mounts. The container sees an empty directory where it can create its own venv.

```
Host (.venv exists):         Container (.venv is empty tmpfs):
~/project/.venv/             ~/project/.venv/  ← empty, create new venv here
~/project/src/       ←────►  ~/project/src/    ← shared
```

## Configuration

### Default (auto-detection)

No configuration needed. Paude scans for `.venv`, `venv`, `.virtualenv`, and `env` directories.

### Manual list

Specify exact directories to shadow:

```json
{
  "venv": [".venv", "my-custom-venv"]
}
```

### Disable

Share venvs with container (will be broken, but available for inspection):

```json
{
  "venv": "none"
}
```

## Recommended Workflow

### Option 1: Manual setup each session

```bash
$ paude
container$ python -m venv .venv
container$ .venv/bin/pip install -r requirements.txt
container$ .venv/bin/python -m pytest
```

### Option 2: Automatic setup with postCreateCommand

```json
{
  "setup": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

### Option 3: Use uv for faster setup

```json
{
  "setup": "uv venv && uv pip install -r requirements.txt"
}
```

## Verification Checklist

- [ ] Host venv works before entering container
- [ ] Container shows empty .venv directory
- [ ] Can create new venv in container
- [ ] Container venv works (pytest, scripts, etc.)
- [ ] Exit container
- [ ] Host venv still works

## Files Changed

- `src/paude/venv.py` - venv detection utilities
- `src/paude/config/models.py` - VenvMode type and PaudeConfig field
- `src/paude/config/parser.py` - paude.json parsing
- `src/paude/mounts.py` - tmpfs mount generation
- `src/paude/container/runner.py` - mount integration
