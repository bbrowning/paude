# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a Podman wrapper that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication.

## Architecture

The project consists of a Python implementation with container definitions:

### Python Package (`src/paude/`)

```
src/paude/
├── __init__.py        # Package with version
├── __main__.py        # Entry point: python -m paude
├── cli.py             # Typer CLI
├── config/            # Configuration parsing
│   ├── detector.py    # Config file detection
│   ├── parser.py      # Config file parsing
│   ├── models.py      # Data models (PaudeConfig, FeatureSpec)
│   └── dockerfile.py  # Dockerfile generation
├── container/         # Container management
│   ├── podman.py      # Podman subprocess wrapper
│   ├── image.py       # Image building and pulling
│   ├── network.py     # Network management
│   └── runner.py      # Container execution
├── features/          # Dev container features
│   ├── downloader.py  # Feature downloading
│   └── installer.py   # Feature installation
├── mounts.py          # Volume mount builder
├── environment.py     # Environment variables
├── hash.py            # Config hashing for caching
├── platform.py        # Platform-specific code (macOS)
├── utils.py           # Utilities
└── dry_run.py         # Dry-run output
```

### Container Definitions

- `containers/paude/` - Main container (Dockerfile, entrypoint.sh) for Claude Code
- `containers/proxy/` - Proxy container (Dockerfile, squid.conf) for network filtering

## Volume Mounts

The script mounts these paths from host to container:
- Current working directory at same path (rw) - preserves real paths for trust prompts
- `~/.config/gcloud` → `/home/paude/.config/gcloud` (ro) - Vertex AI credentials
- `~/.claude` → `/tmp/claude.seed` (ro) - copied into container on startup
- `~/.claude/plugins` → same host path (ro) - plugins use hardcoded paths
- `~/.claude.json` → `/tmp/claude.json.seed` (ro) - copied into container on startup
- `~/.gitconfig` → `/home/paude/.gitconfig` (ro) - Git identity

## Security Model

- No SSH keys mounted - prevents `git push` via SSH
- No GitHub CLI config mounted - prevents `gh` operations
- gcloud credentials are read-only
- Claude config directories are copied in, not mounted - prevents poisoning host config
- Non-root user inside container

## Testing Changes

**All new features must include tests.** This is a hard requirement.

```bash
# Run all tests
make test

# Linting and type checking
make lint
make typecheck

# Rebuild images after container changes
make clean
make run

# Test basic functionality
PAUDE_DEV=1 paude --version
PAUDE_DEV=1 paude --help
```

### Test Locations

- `tests/` - Python tests (pytest)

When adding Python functionality, add tests in `tests/test_<module>.py`.
When adding a new CLI flag, add tests in `tests/test_cli.py`.

## Documentation Requirements

When adding or changing user-facing features (flags, options, behavior):
1. Update `README.md` with the new usage patterns
2. Update the `show_help()` function in `src/paude/cli.py` if adding new flags
3. Keep examples consistent between README and help output

## macOS Considerations

Paths outside `/Users/` require Podman machine configuration. The script detects this and provides guidance when volume mounts fail.

## Feature Development Process

When developing new features, follow this structured approach:

1. **Create feature documentation** in `docs/features/`:
   - Use `PENDING-<feature-name>/` for features in planning (not yet implemented)
   - After implementation, rename to `YYYY-MM-DD-<feature-name>/` using the implementation date
   - Include these files:
     - `RESEARCH.md` - Background research, prior art, compatibility considerations
     - `PLAN.md` - High-level design decisions, security considerations, phased approach
     - `TASKS.md` - Detailed implementation tasks with acceptance criteria
     - `README.md` - Feature overview and verification checklist

2. **Implementation phases**: Break work into logical phases (MVP first, then enhancements)

3. **Testing** (required): Add tests for all new functionality
   - Python code → `tests/test_<module>.py`
   - CLI flags → `tests/test_cli.py`
   - Run `make test` to verify all tests pass

4. **Documentation**: Update README.md and CONTRIBUTING.md with user-facing changes

5. **Rename folder**: After implementation, rename from `PENDING-<feature-name>/` to `YYYY-MM-DD-<feature-name>/`

Example: See `docs/features/2026-01-21-byoc/` for an implemented feature.
Example: See `docs/features/PENDING-config-layering/` for a feature in planning.

## Issue Tracking During Development

When discovering bugs, usability issues, or technical debt unrelated to the current task:
1. Add them to `KNOWN_ISSUES.md` at the project root (create if it doesn't exist)
2. Include: description, reproduction steps if known, and discovery context
3. Continue with the original task
