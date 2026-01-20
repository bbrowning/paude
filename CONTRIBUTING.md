# Contributing to Paude

## Development Setup

### Prerequisites

- [Podman](https://podman.io/getting-started/installation) installed
- Google Cloud SDK configured for Vertex AI (see README.md)
- Git

### Clone and Run

```bash
git clone https://github.com/bbrowning/paude.git
cd paude
```

### Dev Mode

When developing, use `PAUDE_DEV=1` to build images locally instead of pulling from the registry:

```bash
# Using make (recommended)
make run

# Or manually
PAUDE_DEV=1 ./paude

# Check which mode you're in
PAUDE_DEV=1 ./paude --version
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
├── paude              # Main script (bash)
├── Dockerfile         # Claude Code container image
├── entrypoint.sh      # Container entrypoint
├── proxy/
│   ├── Dockerfile     # Squid proxy container image
│   └── squid.conf     # Proxy allowlist configuration
├── Makefile           # Build and release automation
└── README.md
```

## Releasing

Releases are published to Docker Hub (docker.io/bbrowning).

### One-Time Setup

Authenticate with your container registry:

```bash
# For Docker Hub (default)
podman login docker.io

# For other registries, override REGISTRY when publishing
make publish VERSION=0.2.0 REGISTRY=ghcr.io/yourusername
```

### Release Process

```bash
# 1. Ensure you're on main with a clean working tree
git checkout main
git pull origin main
git status  # Should be clean

# 2. Run the release target (updates version in script, creates git tag)
make release V=0.2.0

# 3. Build multi-arch images and push to registry
make publish VERSION=0.2.0

# 4. Push the commit and tag to GitHub
git push origin main --tags

# 5. Create GitHub release
#    Go to: https://github.com/bbrowning/paude/releases/new?tag=v0.2.0
#    - Title: v0.2.0
#    - Attach the 'paude' script as a release asset
#    - Add release notes describing changes
```

### What the Release Does

1. `make release V=x.y.z`:
   - Updates `PAUDE_VERSION` in the paude script
   - Commits the change
   - Creates an annotated git tag `vx.y.z`

2. `make publish VERSION=x.y.z`:
   - Verifies script version matches VERSION
   - Builds multi-arch images (amd64 + arm64)
   - Pushes to docker.io/bbrowning/paude:x.y.z
   - Pushes to docker.io/bbrowning/paude:latest
   - Same for paude-proxy image

### Verifying a Release

After publishing, test the installed experience:

```bash
# Copy script to a directory outside the repo
cp paude /tmp/paude-test
cd /tmp

# Run without PAUDE_DEV (should pull from registry)
./paude-test --version
# Output should show: mode: installed (pulling from docker.io/bbrowning)

# Clean up
rm /tmp/paude-test
```

## Code Style

- Bash scripts: Use shellcheck-compatible patterns
- Keep functions focused and well-named
- Simple single-line comments; avoid decorative comment blocks
- Match the style of surrounding code
