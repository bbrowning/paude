# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a Podman wrapper that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication.

## Architecture

The project consists of a Python implementation with container definitions:

### Python Package (`src/paude/`)

```
src/paude/
├── __init__.py            # Package with version
├── __main__.py            # Entry point: python -m paude
├── cli.py                 # Typer CLI
├── backends/              # Backend implementations
│   ├── base.py            # Backend protocol
│   ├── podman.py          # Podman backend
│   ├── shared.py          # Shared backend utilities
│   └── openshift/         # OpenShift backend
│       ├── backend.py     # OpenShift backend implementation
│       ├── build.py       # Image building on OpenShift
│       ├── config.py      # OpenShift configuration
│       ├── exceptions.py  # OpenShift-specific exceptions
│       ├── oc.py          # oc CLI wrapper
│       ├── proxy.py       # Proxy pod management
│       ├── resources.py   # K8s resource builders
│       └── sync.py        # File synchronization
├── config/                # Configuration parsing
│   ├── claude_layer.py    # Claude config layering
│   ├── detector.py        # Config file detection
│   ├── parser.py          # Config file parsing
│   ├── models.py          # Data models (PaudeConfig, FeatureSpec)
│   └── dockerfile.py      # Dockerfile generation
├── container/             # Container management
│   ├── podman.py          # Podman subprocess wrapper
│   ├── image.py           # Image building and pulling
│   ├── network.py         # Network management
│   ├── runner.py          # Container execution
│   └── volume.py          # Volume management
├── features/              # Dev container features
│   ├── downloader.py      # Feature downloading
│   └── installer.py       # Feature installation
├── domains.py             # Domain aliases and expansion
├── mounts.py              # Volume mount builder
├── environment.py         # Environment variables
├── git_remote.py          # Git remote management
├── hash.py                # Config hashing for caching
├── platform.py            # Platform-specific code (macOS)
├── session_discovery.py   # Session discovery
├── utils.py               # Utilities
├── venv.py                # Venv detection and shadowing
└── dry_run.py             # Dry-run output
```

### Container Definitions

- `containers/paude/` - Main container (Dockerfile, entrypoint.sh, entrypoint-session.sh, credential-watchdog.sh) for Claude Code
- `containers/proxy/` - Proxy container (Dockerfile, entrypoint.sh, squid.conf, ERR_CUSTOM_ACCESS_DENIED) for network filtering

## Volume Mounts

The script mounts these paths from host to container:
- Workspace uses a named volume at `/pvc/workspace` (synced via git remote, not bind-mounted)
- `~/.claude` → `/tmp/claude.seed` (ro) - copied into container on startup
- `~/.claude/plugins` → same host path (ro) - plugins use hardcoded paths
- `~/.claude.json` → `/tmp/claude.json.seed` (ro) - copied into container on startup
- `~/.gitconfig` → `/home/paude/.gitconfig` (ro) - Git identity
- gcloud ADC credentials are injected via Podman secrets, not bind mounts

## Security Model

- No SSH keys mounted - prevents `git push` via SSH
- GitHub CLI (`gh`) is installed but host GH_TOKEN is not auto-propagated; users must use `PAUDE_GITHUB_TOKEN` or `--github-token`
- gcloud credentials are read-only
- Claude config directories are copied in, not mounted - prevents poisoning host config
- Non-root user inside container

## Testing Changes

**All new features must include tests.** This is a hard requirement.

**Before committing any code changes**, always run `make lint` to catch errors early. Do not commit if linting fails.

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

## Code Quality Standards

### File Organization

**Maximum file length: 400 lines** (excluding tests)
- Files over 300 lines: evaluate for splitting
- Files over 400 lines: MUST split before adding new functionality

**When to split files:**
- Multiple unrelated classes in one file
- File handles multiple layers of abstraction
- Class has internal helpers that could stand alone

**How to split:** Create a package directory with `__init__.py` preserving the public API.

### Method/Function Standards

**Maximum method length: 50 lines** (excluding docstrings)
- Methods over 30 lines: evaluate for extraction
- Methods over 50 lines: MUST refactor before adding logic

**Single responsibility:** If you need "and" to describe what a method does, split it.

**Extract when:**
- Logic is repeated (even twice)
- A code block has a comment explaining what it does
- A conditional block exceeds 10 lines

### Class Standards

**Single Responsibility Principle:** A class should have only one reason to change.

**Maximum methods per class: 20** (public + private combined)

**Decompose when:**
- Class file exceeds 400 lines
- Methods group into 2+ distinct categories
- Testing requires mocking many unrelated dependencies

### Code Reuse

**No duplication:** If code appears twice, extract to shared function.

**Shared utilities locations:**
- Cross-cutting: `src/paude/utils.py`
- Backend-shared: `src/paude/backends/shared.py`

### Abstraction Patterns

**Protocols:** Use when 2+ implementations share the same API.

**Builders:** Use for complex object construction (K8s specs, CLI arguments).

**Dependency injection:** Accept dependencies via `__init__` for testability.

### Refactoring Triggers

| Metric | Threshold | Action |
|--------|-----------|--------|
| File length | > 400 lines | Split file |
| Method length | > 50 lines | Extract methods |
| Class methods | > 20 | Decompose class |
| Duplicated code | 2+ occurrences | Extract to shared |

### Testability

- Accept dependencies via constructor, not created internally
- Keep I/O at edges; business logic as pure functions
- Wrap external commands (podman, oc) in testable classes

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
