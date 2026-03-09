# Contributing to Paude

## Development Setup

### Prerequisites

- [Podman](https://podman.io/getting-started/installation) installed
- Python 3.11+ (for the Python implementation)
- Google Cloud SDK configured for Vertex AI (see README.md)
- Git

### Clone and Run

```bash
git clone https://github.com/bbrowning/paude.git
cd paude
```

### Python Development Setup

The paude CLI is implemented in Python. To set up the development environment:

```bash
# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install in development mode with all dev dependencies
make install
# or: uv pip install -e ".[dev]"
```

### Dev Mode

When developing, use `PAUDE_DEV=1` to build images locally instead of pulling from the registry:

```bash
# Using make (recommended)
make run

# Or manually
PAUDE_DEV=1 paude

# Check which mode you're in
PAUDE_DEV=1 paude --version
# Output: paude 0.1.0
#         mode: development (PAUDE_DEV=1, building locally)
```

### Make Targets

```bash
make help      # Show all targets
make build     # Build images locally (without running)
make run       # Build and run in dev mode
make clean     # Remove local images
```

### Testing Changes

**All new features must include tests.** Run the test suite before submitting changes:

```bash
make test        # Run all tests
make lint        # Check code style with ruff
make typecheck   # Run mypy type checker
make format      # Format code with ruff
```

Test locations:
- `tests/` - Python tests (pytest)

When adding Python functionality, add tests in `tests/test_<module>.py`.
When adding a new CLI flag, add tests in `tests/test_cli.py`.

After modifying the Dockerfile or proxy configuration:

```bash
# Remove existing images to force rebuild
make clean

# Run in dev mode to rebuild
make run
```

## Project Structure

```
paude/
├── src/paude/                 # Python implementation
│   ├── __init__.py            # Package with version
│   ├── __main__.py            # Entry point: python -m paude
│   ├── cli.py                 # Typer CLI
│   ├── backends/              # Backend implementations
│   │   ├── base.py            # Backend protocol
│   │   ├── podman.py          # Podman backend
│   │   ├── shared.py          # Shared backend utilities
│   │   └── openshift/         # OpenShift backend
│   │       ├── backend.py     # OpenShift backend implementation
│   │       ├── build.py       # Image building on OpenShift
│   │       ├── config.py      # OpenShift configuration
│   │       ├── exceptions.py  # OpenShift-specific exceptions
│   │       ├── oc.py          # oc CLI wrapper
│   │       ├── proxy.py       # Proxy pod management
│   │       ├── resources.py   # K8s resource builders
│   │       └── sync.py        # File synchronization
│   ├── config/                # Configuration parsing
│   │   ├── claude_layer.py    # Claude config layering
│   │   ├── detector.py        # Config file detection
│   │   ├── parser.py          # Config file parsing
│   │   ├── models.py          # Data models
│   │   └── dockerfile.py      # Dockerfile generation
│   ├── container/             # Container management
│   │   ├── podman.py          # Podman wrapper
│   │   ├── build_context.py   # Build context preparation
│   │   ├── image.py           # Image management
│   │   ├── network.py         # Network management
│   │   ├── runner.py          # Container execution
│   │   └── volume.py          # Volume management
│   ├── features/              # Dev container features
│   │   ├── downloader.py      # Feature downloading
│   │   └── installer.py       # Feature installation
│   ├── constants.py           # Shared constants
│   ├── domains.py             # Domain aliases and expansion
│   ├── mounts.py              # Volume mount builder
│   ├── environment.py         # Environment variables
│   ├── git_remote.py          # Git remote management
│   ├── hash.py                # Config hashing
│   ├── platform.py            # Platform-specific code
│   ├── proxy_log.py           # Proxy log parsing
│   ├── session_discovery.py   # Session discovery
│   ├── session_status.py      # Session status tracking
│   ├── workflow.py            # Orchestration workflow (harvest, reset)
│   └── dry_run.py             # Dry-run output
├── containers/
│   ├── paude/
│   │   ├── Dockerfile             # Claude Code container image
│   │   ├── entrypoint.sh          # Container entrypoint
│   │   ├── entrypoint-session.sh  # Session entrypoint
│   │   └── credential-watchdog.sh # Credential refresh watchdog
│   └── proxy/
│       ├── Dockerfile             # Squid proxy container image
│       ├── entrypoint.sh          # Proxy container entrypoint
│       ├── squid.conf             # Proxy allowlist configuration
│       └── ERR_CUSTOM_ACCESS_DENIED  # Custom error page
├── tests/                 # Python tests (pytest)
├── examples/              # Example configurations
├── docs/                  # Documentation
├── pyproject.toml         # Python project configuration
├── Makefile               # Build and release automation
└── README.md
```

## Releasing

Releases are published automatically via GitHub Actions to:
- **PyPI** (pypi.org/project/paude) - Python package
- **Quay.io** (quay.io/bbrowning) - Container images
- **GitHub Releases** - Release notes

### One-Time Setup

These steps only need to be done once per repository:

1. **PyPI Trusted Publisher**: Go to pypi.org → project "paude" → Publishing → Add GitHub as a trusted publisher:
   - Owner: `bbrowning`
   - Repository: `paude`
   - Workflow: `release.yml`
   - Environment: `pypi`

2. **GitHub Environment**: Create a `pypi` environment in GitHub repo settings (Settings → Environments → New environment → name it `pypi`)

3. **Quay.io Robot Account**: Create a robot account on Quay.io with push access to the `bbrowning` namespace, then add these as GitHub repo secrets (Settings → Secrets and variables → Actions):
   - `QUAY_USERNAME` - Robot account username
   - `QUAY_PASSWORD` - Robot account password/token

### Release Process

```bash
# 1. Ensure you're on main with a clean working tree
git checkout main
git pull origin main
git status  # Should be clean

# 2. Run tests to verify everything works
make test

# 3. Update version and create git tag
make release VERSION=0.6.0

# 4. Push the commit and tag to GitHub
git push origin main --tags

# Done! GitHub Actions handles the rest:
#   - Runs tests
#   - Builds and pushes container images to Quay.io
#   - Builds and publishes Python package to PyPI
#   - Creates a GitHub release with auto-generated notes
```

### What Happens Automatically

When a tag matching `v*` is pushed, the `.github/workflows/release.yml` workflow:

1. **Tests** - Runs lint, type check, and unit tests across Python 3.11 and 3.12
2. **Container images** - Builds multi-arch images (amd64 + arm64) and pushes versioned + `latest` tags to Quay.io
3. **PyPI** - Builds and publishes the Python package using OIDC trusted publishing (no API token needed)
4. **GitHub Release** - Creates a release with auto-generated notes from commits since the last tag

### What `make release` Does Locally

`make release VERSION=x.y.z`:
- Updates version in `pyproject.toml` and `src/paude/__init__.py`
- Regenerates `uv.lock`
- Commits the version change
- Creates an annotated git tag `vx.y.z`

### Manual Release (Fallback)

If you need to publish manually (e.g., CI is down):

```bash
# Container images
make publish VERSION=x.y.z

# PyPI
make pypi-build
make pypi-publish
```

### Verifying a Release

After the GitHub Actions workflow completes:

1. Check the workflow run at: https://github.com/bbrowning/paude/actions/workflows/release.yml
2. Verify container images on Quay.io
3. Test the PyPI package:

```bash
uv venv /tmp/test-paude
source /tmp/test-paude/bin/activate
uv pip install paude
paude --version
paude --help
deactivate
rm -rf /tmp/test-paude
```

## Code Style

- Use type hints throughout (Python 3.11+ syntax: `list[str]` not `List[str]`)
- Run `make lint` before committing (uses ruff)
- Run `make format` to auto-format code
- Run `make typecheck` to verify types (uses mypy in strict mode)
- Follow existing patterns in the codebase

## Code Quality Standards

This project enforces strict code quality standards to maintain long-term maintainability:

- **File size:** Maximum 400 lines (evaluate splitting at 300+)
- **Method size:** Maximum 50 lines (evaluate extraction at 30+)
- **Class size:** Maximum 20 methods per class
- **No duplication:** Extract repeated code to shared utilities

For detailed standards including abstraction patterns, refactoring triggers, and testability requirements, see `.claude/CLAUDE.md`.

