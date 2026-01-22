# Python venv Isolation - Research

## Problem Statement

Python virtual environments created on the host cannot be used inside the paude container because:

1. **Absolute paths in pyvenv.cfg**: The `home` key points to host Python location
   ```
   home = /Users/bbrowning/.pyenv/versions/3.12.9/bin
   ```

2. **Symlinks to host binaries**: `.venv/bin/python` symlinks to host Python
   ```
   .venv/bin/python → /Users/bbrowning/.pyenv/versions/3.12.9/bin/python3.12
   ```

3. **Hardcoded shebangs**: Installed package scripts contain host paths
   ```
   #!/Users/bbrowning/project/.venv/bin/python
   ```

4. **Platform-specific binaries**: `.so` files compiled for macOS arm64 won't work on Linux aarch64

## Key Insight

Virtual environments are **designed to be disposable and non-relocatable** (PEP 405). The solution is not to make them portable, but to keep host and container venvs separate.

## Prior Art

### VS Code Dev Containers
- Creates venv inside container during build
- Does not share venvs between host and container
- Uses `postCreateCommand` to set up container venv

### Docker/Podman Best Practices
- Official recommendation: Don't bind-mount venvs from host
- Create venv in container at build time or startup
- Use lockfiles (poetry.lock, requirements.txt) to ensure reproducibility

### uv Documentation
> "It is important not to include the project virtual environment (.venv) in bind mounts during development"

## venv Detection

A directory is a venv if:
1. Contains `pyvenv.cfg` file (definitive marker from PEP 405)
2. Contains `bin/python` (or `Scripts/python.exe` on Windows)

Common venv directory names:
- `.venv` (modern convention, recommended by PEP 582 discussions)
- `venv` (older convention)
- `.virtualenv` (virtualenv tool)
- `env` (legacy)

## Podman Mount Capabilities

Podman supports "shadowing" a subdirectory:
```bash
# Mount workspace, then shadow .venv with tmpfs
podman run \
  -v /project:/project:rw \
  --mount type=tmpfs,destination=/project/.venv \
  image
```

The second mount shadows the first for that path. The container sees:
- `/project/src` - from host (shared)
- `/project/.venv` - empty tmpfs (container-local)

## Related Technologies

### Poetry
- `poetry config virtualenvs.in-project true` creates `.venv` in project
- `poetry install` recreates venv from lockfile
- Works well with this approach

### uv
- Faster venv creation (10-100x)
- `uv venv` and `uv pip install` are drop-in replacements
- Consider recommending uv for container use

### pyenv
- Often used on macOS for Python version management
- Creates venvs with paths like `/Users/.../.pyenv/versions/...`
- Same problem applies
