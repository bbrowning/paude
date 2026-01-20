# BYOC Plan: Bring Your Own Container for paude

This document outlines the implementation plan for enabling paude users to customize their container environment while maintaining paude's security guarantees.

## Design Goals

1. **Zero-friction onboarding**: Projects without configuration should work with sensible defaults
2. **Leverage existing standards**: Support devcontainer.json so users can reuse existing configs
3. **Maintain security model**: paude's network isolation and mount controls must remain intact
4. **Progressive disclosure**: Simple use cases should be simple; advanced customization available when needed
5. **Build-time customization**: Container customization happens at build time, not runtime

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     paude CLI                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Detect configuration (.devcontainer/devcontainer.json)  │
│                         │                                    │
│                         ▼                                    │
│  2. Parse configuration (image, dockerfile, features)       │
│                         │                                    │
│                         ▼                                    │
│  3. Generate paude-compatible Dockerfile                    │
│     - Start from user's base image                          │
│     - Apply dev container features                          │
│     - Add paude requirements (user, entrypoint, etc.)       │
│                         │                                    │
│                         ▼                                    │
│  4. Build image with podman                                  │
│                         │                                    │
│                         ▼                                    │
│  5. Run with paude security controls                        │
│     (network isolation, mount controls, proxy)              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Configuration Detection Hierarchy

paude will look for configuration in this order:

1. `.devcontainer/devcontainer.json` (standard location)
2. `.devcontainer.json` (root-level alternative)
3. `paude.json` (paude-native format, simpler subset)
4. **Default**: Use `paude:latest` base image

## Supported devcontainer.json Properties

### Phase 1: Core Properties

| Property | Description | Notes |
|----------|-------------|-------|
| `image` | Base container image | Required if no `build` specified |
| `build.dockerfile` | Path to Dockerfile | Relative to devcontainer.json |
| `build.context` | Build context directory | Defaults to devcontainer.json directory |
| `build.args` | Build arguments | Passed to `podman build --build-arg` |
| `features` | Dev container features | See features section below |

### Phase 2: Lifecycle & Environment

| Property | Description | Notes |
|----------|-------------|-------|
| `postCreateCommand` | Run after container creation | For project setup (npm install, etc.) |
| `containerEnv` | Environment variables | Set in container |

### Explicitly NOT Supported

| Property | Reason |
|----------|--------|
| `customizations.vscode.*` | VS Code specific |
| `forwardPorts` | paude is CLI-based |
| `mounts` | Security - paude controls mounts |
| `runArgs` | Security - paude controls run args |
| `privileged` | Security - never allow privileged |
| `capAdd` | Security - paude controls capabilities |
| `remoteUser` | Security - paude always uses `paude` user |

## Dev Container Features Support

Features are self-contained installation scripts from the [devcontainers/features](https://github.com/devcontainers/features) repository.

### Implementation Strategy

1. **Parse features from devcontainer.json**
2. **Download feature archives** from ghcr.io
3. **Execute feature install.sh** in build context
4. **Layer feature installation** on base image

### Example Feature Usage

```json
{
  "image": "debian:bookworm-slim",
  "features": {
    "ghcr.io/devcontainers/features/python:1": {
      "version": "3.11"
    },
    "ghcr.io/devcontainers/features/go:1": {
      "version": "1.21"
    }
  }
}
```

### Feature Installation Order

Features are installed in the order specified. paude will respect `overrideFeatureInstallOrder` if provided.

## paude.json: Native Simple Format

For users who don't need full devcontainer.json compatibility, offer a simpler format:

```json
{
  "base": "python:3.11-slim",
  "packages": ["git", "make", "gcc", "curl"],
  "setup": "pip install -r requirements.txt"
}
```

Mapping to devcontainer.json:
- `base` → `image`
- `packages` → feature that runs `apt-get install`
- `setup` → `postCreateCommand`

## Image Naming and Caching

### Image Tags

Built images use deterministic tags based on config hash:

```
paude-workspace:<config-hash>
```

This enables:
- Cache reuse when config unchanged
- Multiple projects with different configs
- Easy cleanup of stale images

### Cache Invalidation

Rebuild triggered when:
- devcontainer.json content changes
- Referenced Dockerfile changes
- `paude --rebuild` flag used

## Security Layer

All user-provided container configs run through paude's security layer:

### Required Additions to User Images

1. **Non-root user**: Create `paude` user if not exists
2. **Entrypoint wrapper**: Our entrypoint.sh that copies configs
3. **Verification**: Ensure required tools (git, claude-code) installed

### Runtime Controls (Unchanged)

- Network isolation via proxy (unless `--allow-network`)
- Mount controls (workspace + read-only configs)
- No SSH keys, no GitHub CLI config

## User Experience Flow

### New Project (No Config)

```bash
$ cd my-project
$ paude
# Uses default paude image, works immediately
```

### Project with devcontainer.json

```bash
$ cd my-project  # Has .devcontainer/devcontainer.json
$ paude
Detected .devcontainer/devcontainer.json
Building workspace image... (cached)
Starting claude with Python 3.11, Go 1.21...
```

### First-time Setup

```bash
$ cd my-project
$ paude
Detected .devcontainer/devcontainer.json
Building workspace image...
  → Using base: python:3.11-slim
  → Installing feature: python
  → Installing feature: go
  → Adding paude requirements
  → Running postCreateCommand: pip install -r requirements.txt
Build complete (cached as paude-workspace:a1b2c3d)
Starting claude...
```

### Rebuild When Needed

```bash
$ paude --rebuild
Rebuilding workspace image...
```

## Error Handling

### Missing Required Tools

If user's image missing required tools:

```
Error: Base image missing required tool: git
Add git to your Dockerfile or use a feature:
  "features": {
    "ghcr.io/devcontainers/features/git:1": {}
  }
```

### Feature Installation Failure

```
Error: Failed to install feature ghcr.io/devcontainers/features/python:1
  → Check network connectivity
  → Verify feature version exists
  → See logs at ~/.cache/paude/build.log
```

### Unsupported Properties

```
Warning: Ignoring unsupported property 'mounts' in devcontainer.json
  → paude controls mounts for security
  → Workspace is mounted at current directory
```

## Testing Strategy

1. **Unit tests**: Config parsing, Dockerfile generation
2. **Integration tests**: Build and run with various configs
3. **E2E tests**: Full workflow with sample projects

### Sample Projects for Testing

- Python project with requirements.txt
- Node.js project with package.json
- Go project with go.mod
- Multi-language project
- Project with existing devcontainer.json

## Rollout Plan

### Phase 1: Foundation
- Config detection and parsing
- Simple `image` property support
- Basic error handling

### Phase 2: Features
- Dev container features support
- Feature caching
- Build optimization

### Phase 3: Lifecycle
- postCreateCommand support
- Environment variables
- Advanced build args

### Phase 4: Polish
- Better error messages
- Build progress indication
- Documentation and examples

## Migration Path

Existing paude users:
- **No action required**: Default behavior unchanged
- **Opt-in customization**: Add devcontainer.json when needed

Projects with existing devcontainer.json:
- **Automatic detection**: paude uses existing config
- **Warnings for unsupported**: Clear messages about ignored properties

## Open Questions

1. **Should we support docker-compose.yml?** Multi-container setups could be useful but add complexity.
2. **Feature caching strategy?** Download features once globally or per-project?
3. **How to handle features requiring network?** Features need internet during build, but we restrict runtime network.

## Next Steps

See BYOC-TASKS.md for the implementation task breakdown.
