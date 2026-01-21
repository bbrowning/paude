# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a Podman wrapper that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication.

## Architecture

The project consists of a main script, library modules, and container definitions:

- `paude` - Main bash script that validates environment, builds container images, and runs Claude Code
- `lib/` - Bash library modules sourced by the main script
  - `config.sh` - Configuration detection and parsing (devcontainer.json, paude.json)
  - `hash.sh` - Deterministic hash computation for image caching
  - `features.sh` - Dev container feature download and installation
- `containers/paude/` - Main container artifacts (Dockerfile, entrypoint.sh) for Claude Code
- `containers/proxy/` - Proxy container artifacts (Dockerfile, entrypoint.sh, squid.conf) for network filtering
- `tests/` - Test suite for the main paude script
- `test/` - Unit tests for library modules

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
# Run all tests (must pass before merging)
make test

# Rebuild images after container changes
make clean
make run

# Test basic functionality
PAUDE_DEV=1 ./paude --version
PAUDE_DEV=1 ./paude --help
```

### Test Locations

- `tests/` - Integration tests for CLI flags, argument parsing, mounts
- `test/` - Unit tests for library modules (config.sh, hash.sh, features.sh)

When adding a new CLI flag, add tests in `tests/test_cli_args.sh`.
When adding a new library module, add tests in `test/test_<module>.sh`.

## Documentation Requirements

When adding or changing user-facing features (flags, options, behavior):
1. Update `README.md` with the new usage patterns
2. Update the `show_help()` function in `paude` if adding new flags
3. Keep examples consistent between README and help output

## macOS Considerations

Paths outside `/Users/` require Podman machine configuration. The script detects this and provides guidance when volume mounts fail.

## Feature Development Process

When developing new features, follow this structured approach:

1. **Create feature documentation** in `docs/features/<feature-name>/`:
   - `RESEARCH.md` - Background research, prior art, compatibility considerations
   - `PLAN.md` - High-level design decisions, security considerations, phased approach
   - `TASKS.md` - Detailed implementation tasks with acceptance criteria
   - `README.md` - Feature overview and verification checklist

2. **Implementation phases**: Break work into logical phases (MVP first, then enhancements)

3. **Testing** (required): Add tests for all new functionality
   - CLI flags/options → `tests/test_cli_args.sh`
   - Library modules → `test/test_<module>.sh`
   - Run `make test` to verify all tests pass

4. **Documentation**: Update README.md and CONTRIBUTING.md with user-facing changes

Example: See `docs/features/byoc/` for the BYOC (Bring Your Own Container) feature documentation.

