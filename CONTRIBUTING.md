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
# or: pip install -e ".[dev]"
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
├── src/paude/             # Python implementation
│   ├── __init__.py        # Package with version
│   ├── __main__.py        # Entry point: python -m paude
│   ├── cli.py             # Typer CLI
│   ├── config/            # Configuration parsing
│   │   ├── detector.py    # Config file detection
│   │   ├── parser.py      # Config file parsing
│   │   ├── models.py      # Data models
│   │   └── dockerfile.py  # Dockerfile generation
│   ├── container/         # Container management
│   │   ├── podman.py      # Podman wrapper
│   │   ├── image.py       # Image management
│   │   ├── network.py     # Network management
│   │   └── runner.py      # Container execution
│   ├── features/          # Dev container features
│   │   ├── downloader.py  # Feature downloading
│   │   └── installer.py   # Feature installation
│   ├── mounts.py          # Volume mount builder
│   ├── environment.py     # Environment variables
│   ├── hash.py            # Config hashing
│   ├── platform.py        # Platform-specific code
│   ├── utils.py           # Utilities
│   └── dry_run.py         # Dry-run output
├── containers/
│   ├── paude/
│   │   ├── Dockerfile     # Claude Code container image
│   │   └── entrypoint.sh  # Container entrypoint
│   └── proxy/
│       ├── Dockerfile     # Squid proxy container image
│       ├── entrypoint.sh  # Proxy container entrypoint
│       └── squid.conf     # Proxy allowlist configuration
├── tests/                 # Python tests (pytest)
├── examples/              # Example configurations
├── docs/
│   └── features/          # Feature development documentation
├── pyproject.toml         # Python project configuration
├── Makefile               # Build and release automation
└── README.md
```

## Releasing

Releases are published to Quay.io (quay.io/bbrowning).

### One-Time Setup

Authenticate with your container registry:

```bash
# For Quay.io (default)
podman login quay.io
```

### Release Process

```bash
# 1. Ensure you're on main with a clean working tree
git checkout main
git pull origin main
git status  # Should be clean

# 2. Update version in pyproject.toml and create git tag
make release VERSION=0.4.0

# 3. Build multi-arch images and push to registry
make publish VERSION=0.4.0

# 4. Push the commit and tag to GitHub
git push origin main --tags

# 5. Create GitHub release
#    Go to: https://github.com/bbrowning/paude/releases/new?tag=v0.4.0
#    - Title: v0.4.0
#    - Add release notes describing changes
```

### What the Release Does

1. `make release VERSION=x.y.z`:
   - Updates version in pyproject.toml
   - Commits the change
   - Creates an annotated git tag `vx.y.z`

2. `make publish VERSION=x.y.z`:
   - Builds multi-arch images (amd64 + arm64)
   - Pushes to quay.io/bbrowning/paude-claude-centos9:x.y.z
   - Pushes to quay.io/bbrowning/paude-claude-centos9:latest
   - Same for paude-proxy-centos9 image

### Verifying a Release

After publishing, test the installed experience:

```bash
# Install from PyPI
pip install paude

# Test basic commands
paude --version
paude --help
```

## Code Style

- Use type hints throughout (Python 3.11+ syntax: `list[str]` not `List[str]`)
- Run `make lint` before committing (uses ruff)
- Run `make format` to auto-format code
- Run `make typecheck` to verify types (uses mypy in strict mode)
- Follow existing patterns in the codebase

## Adding New Features

For significant features, follow the structured development process:

1. Create documentation in `docs/features/<feature-name>/`:
   - `RESEARCH.md` - Background research and prior art
   - `PLAN.md` - Design decisions and phased approach
   - `TASKS.md` - Implementation tasks with acceptance criteria
   - `README.md` - Overview and verification checklist

2. Implement in phases (MVP first, then enhancements)

3. **Add tests** (required):
   - Add tests in `tests/test_<module>.py`
   - Run `make test` to verify all tests pass

4. Update README.md and this file with user-facing changes

See `docs/features/byoc/` for an example of this process.
