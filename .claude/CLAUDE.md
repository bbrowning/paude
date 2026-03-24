# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a container wrapper that runs AI coding agents (Claude Code, Gemini CLI) in isolated, secure containers with Google Vertex AI authentication.

## Architecture

The project consists of a Python implementation with container definitions:

### Python Package (`src/paude/`)

```
src/paude/
├── __init__.py            # Package with version
├── __main__.py            # Entry point: python -m paude
├── agents/                # Agent definitions
│   ├── __init__.py        # Agent registry (get_agent, list_agents)
│   ├── base.py            # Agent protocol and AgentConfig
│   ├── claude.py          # Claude Code agent
│   ├── cursor.py          # Cursor CLI agent
│   └── gemini.py          # Gemini CLI agent
├── backends/              # Backend implementations
│   ├── base.py            # Backend protocol
│   ├── shared.py          # Shared backend utilities
│   ├── podman/            # Podman/Docker backend
│   │   ├── backend.py     # PodmanBackend implementation
│   │   ├── exceptions.py  # Podman-specific exceptions
│   │   ├── helpers.py     # Helper functions
│   │   └── proxy.py       # Proxy management
│   └── openshift/         # OpenShift backend
│       ├── backend.py     # OpenShiftBackend implementation
│       ├── build.py       # Image building on OpenShift
│       ├── config.py      # OpenShift configuration
│       ├── exceptions.py  # OpenShift-specific exceptions
│       ├── oc.py          # oc CLI wrapper
│       ├── pods.py        # Pod query helpers
│       ├── proxy.py       # Proxy pod management
│       ├── resources.py   # K8s resource builders
│       ├── session_connection.py  # Session connection management
│       ├── session_domains.py     # Domain management
│       ├── session_lifecycle.py   # Session create/delete/start/stop
│       ├── session_lookup.py      # Session queries and discovery
│       └── sync.py        # File synchronization
├── cli/                   # CLI implementation
│   ├── app.py             # Typer app definition
│   ├── commands.py        # Session commands (delete, start, stop, etc.)
│   ├── config_cmd.py      # Configuration commands
│   ├── create.py          # Session create command
│   ├── create_openshift.py # OpenShift-specific create options
│   ├── create_podman.py   # Podman-specific create options
│   ├── domains.py         # Domain CLI helpers
│   ├── helpers.py         # Shared CLI helpers
│   ├── help.py            # Custom help and reference sections
│   ├── remote.py          # Git remote commands
│   ├── remote_git_setup.py # Git remote setup
│   └── status.py          # Status, reset, and harvest commands
├── config/                # Configuration parsing
│   ├── claude_layer.py    # Agent config layering
│   ├── detector.py        # Config file detection
│   ├── dockerfile.py      # Dockerfile generation
│   ├── models.py          # Data models (PaudeConfig, FeatureSpec)
│   ├── parser.py          # Config file parsing
│   ├── resolver.py        # Config resolution with provenance
│   └── user_config.py     # User config defaults and persistence
├── container/             # Container management
│   ├── build_context.py   # Build context preparation
│   ├── engine.py          # Container engine abstraction
│   ├── image.py           # Image building and pulling
│   ├── network.py         # Network management
│   ├── podman.py          # Podman subprocess wrapper
│   ├── proxy_runner.py    # Proxy container execution
│   ├── runner.py          # Container execution
│   └── volume.py          # Volume management
├── features/              # Dev container features
│   ├── downloader.py      # Feature downloading
│   └── installer.py       # Feature installation
├── git_remote/            # Git remote management
│   ├── container_ops.py   # Container workspace git setup
│   ├── exec_cmd.py        # Execution command builders
│   └── utils.py           # Git remote URL utilities
├── transport/             # Command transport (local/SSH)
│   ├── base.py            # Transport protocol
│   ├── config_sync.py     # Config file sync over SSH
│   ├── local.py           # Local transport via subprocess
│   └── ssh.py             # SSH transport for remote execution
├── constants.py           # Shared constants
├── domains.py             # Domain aliases and expansion
├── dry_run.py             # Dry-run output
├── environment.py         # Environment variables
├── hash.py                # Config hashing for caching
├── mounts.py              # Volume mount builder
├── platform.py            # Platform-specific code (macOS)
├── proxy_log.py           # Proxy log parsing
├── registry.py            # Local session registry
├── session_discovery.py   # Session discovery
├── session_status.py      # Session status tracking
└── workflow.py            # Orchestration workflow (harvest, reset)
```

### Container Definitions

- `containers/paude/` - Main container (Dockerfile, entrypoint.sh, entrypoint-session.sh, credential-watchdog.sh) for AI coding agents
- `containers/proxy/` - Proxy container (Dockerfile, entrypoint.sh, squid.conf, ERR_CUSTOM_ACCESS_DENIED) for network filtering

## Volume Mounts

The script mounts these paths from host to container (mount paths vary by agent):
- Workspace uses a named volume at `/pvc/workspace` (synced via git remote, not bind-mounted)
- Agent config directories (e.g., `~/.claude` for Claude Code) → copied into container on startup
- `~/.gitconfig` → `/home/paude/.gitconfig` (ro) - Git identity
- gcloud ADC credentials are injected via Podman secrets, not bind mounts

## Security Model

- No SSH keys mounted - prevents `git push` via SSH
- GitHub CLI (`gh`) is installed but host GH_TOKEN is not auto-propagated; users must use `PAUDE_GITHUB_TOKEN` or `--github-token`
- gcloud credentials are read-only
- Agent config directories are copied in, not mounted - prevents poisoning host config
- Non-root user inside container

## Testing Changes

**All new features must include tests.** This is a hard requirement.

**Before committing any code changes**, always run `make lint` and `make typecheck` to catch errors early. Do not commit if either fails.

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
- Cross-cutting: `src/paude/constants.py`
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
2. Update the `show_help()` function in `src/paude/cli/help.py` if adding new flags
3. Keep examples consistent between README and help output

## Documentation Language

Use agent-agnostic language in all user-facing text (README, docs/, CLI help). Say "the agent" or "your agent" instead of "Claude". Reference specific agents only in examples or when discussing agent-specific configuration.

## Issue Tracking During Development

When discovering bugs, usability issues, or technical debt unrelated to the current task:
1. Add them to `KNOWN_ISSUES.md` at the project root (create if it doesn't exist)
2. Include: description, reproduction steps if known, and discovery context
3. Continue with the original task
