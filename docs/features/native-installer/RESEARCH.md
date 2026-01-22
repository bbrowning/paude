# Native Installer Migration - Research

## Problem Statement

Paude currently installs Claude Code using the npm installer inside the container:

```dockerfile
RUN npm install -g @anthropic-ai/claude-code@latest
```

However, the npm installer is now **deprecated**. Anthropic recommends the native installer, which provides:

- Automatic updates (no manual `npm upgrade` required)
- Bundled dependencies (includes ripgrep)
- No Node.js version dependency at runtime
- Simpler installation paths (`~/.local/bin/claude`)

If we continue using npm, users will miss out on auto-updates and eventually face compatibility issues as npm support is deprioritized.

## Native Installer Details

### Installation Methods

**Unix (macOS, Linux, WSL):**
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Windows PowerShell:**
```powershell
irm https://claude.ai/install.ps1 | iex
```

### Installation Paths

| Component | npm Installer | Native Installer |
|-----------|---------------|------------------|
| Binary | Global npm path (`/usr/local/bin/claude`) | `~/.local/bin/claude` |
| Data | npm package location | `~/.local/share/claude` |
| Updates | Manual (`npm upgrade`) | Automatic or `claude update` |

### Auto-Update Configuration

Native installer supports configurable release channels:

```json
{
  "autoUpdatesChannel": "stable"
}
```

Options:
- `"latest"` (default): New features immediately upon release
- `"stable"`: Version approximately one week old, skipping major regressions

Auto-updates can be disabled:
```bash
export DISABLE_AUTOUPDATER=1
```

### Migration Path

Existing npm installations can migrate using:
```bash
claude install
```

This installs the native version alongside npm version, with native taking precedence in PATH.

## Container Considerations

### Current Approach (npm)

```dockerfile
FROM node:20-slim

# System dependencies
RUN apt-get update && apt-get install -y ripgrep git ...

# Install Claude Code via npm
RUN npm install -g @anthropic-ai/claude-code@latest
```

Pros:
- Simple, familiar Dockerfile pattern
- Works with existing base images

Cons:
- npm package is deprecated
- No auto-updates (container is ephemeral anyway)
- Requires Node.js in image
- Requires separate ripgrep installation

### Native Installer Approach

```dockerfile
FROM debian:bookworm-slim

# System dependencies (no Node.js needed!)
RUN apt-get update && apt-get install -y git curl ...

# Install Claude Code via native installer
RUN curl -fsSL https://claude.ai/install.sh | bash

# Add to PATH
ENV PATH="/root/.local/bin:$PATH"
```

Pros:
- No Node.js dependency
- Smaller image size potential
- Aligned with Anthropic's recommended approach
- Bundled ripgrep

Cons:
- `curl | bash` pattern in Dockerfile (common but less auditable)
- Installs to user-specific path (`~/.local/bin`)
- Need to handle non-root user installation

### Auto-Updates in Container Context

Auto-updates are **not relevant** for paude's container use case because:
- Container is ephemeral (recreated each session)
- Image is rebuilt to get new versions
- Auto-updater would waste startup time

We should **disable auto-updates** in the container:
```dockerfile
ENV DISABLE_AUTOUPDATER=1
```

And instead update by rebuilding the image with latest installer.

## User Path Considerations

Current container uses `paude` user (non-root). Native installer installs to:
- Root: `/root/.local/bin/claude`
- Non-root: `/home/paude/.local/bin/claude`

Need to ensure:
1. Installer runs as correct user
2. PATH includes the installation directory

## Compatibility with Existing Setup

### What Changes

| Aspect | Current (npm) | Native |
|--------|---------------|--------|
| Base image | `node:20-slim` | Can use `debian:slim` |
| Install command | `npm install -g @anthropic-ai/claude-code` | `curl ... \| bash` |
| Binary location | `/usr/local/bin/claude` | `~/.local/bin/claude` |
| ripgrep | Separate apt install | Bundled |
| Node.js | Required | Not required |

### What Stays the Same

- Entry point and startup scripts
- Volume mounts and configuration
- Environment variables (VERTEX_AI, etc.)
- Proxy configuration
- User permissions

## Alternative Approaches

### Option A: Direct Binary Download

Instead of running the installer script, we could:
1. Download the binary directly from a known URL
2. Place it in a standard location
3. Skip the installer overhead

Pros:
- More explicit, auditable
- No bash script execution
- Predictable paths

Cons:
- Need to track binary URL (may change)
- May miss bundled components
- Fighting the official approach

### Option B: Hybrid (npm for now, migrate later)

Continue with npm but plan migration when:
- npm package is formally EOL'd
- Native installer is more container-friendly

Cons:
- Kicks the can down the road
- npm will receive less attention
- May miss fixes that only ship in native

### Option C: Multi-stage Build

```dockerfile
# Stage 1: Install Claude Code
FROM debian:bookworm-slim AS claude-install
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL https://claude.ai/install.sh | bash

# Stage 2: Runtime image
FROM debian:bookworm-slim
COPY --from=claude-install /root/.local/bin/claude /usr/local/bin/claude
COPY --from=claude-install /root/.local/share/claude /usr/local/share/claude
```

Pros:
- Cleaner final image
- Installer script doesn't persist
- Can use any base for final stage

Cons:
- More complex Dockerfile
- Need to identify all files to copy
- May break if installer changes directory structure

## Installer Script Analysis

The native installer script should be reviewed for:
1. What files it installs and where
2. Whether it requires interactive input (breaks Dockerfile)
3. Whether it can be run non-interactively
4. What dependencies it requires (curl, bash, etc.)

From the documentation, the installer appears to be non-interactive and suitable for CI/container use.

## Recommendation

**Proceed with native installer migration** using a straightforward approach:

1. Switch base image from `node:20-slim` to `debian:bookworm-slim`
2. Use official installer script in Dockerfile
3. Disable auto-updates via environment variable
4. Update PATH to include `~/.local/bin`
5. Test thoroughly before releasing

This aligns with Anthropic's direction while keeping the Dockerfile simple.

## Open Questions

1. **Installer idempotency**: Can we safely run the installer on each build, or should we cache?
2. **Version pinning**: Is there a way to install a specific version? (For reproducibility)
3. **Checksum verification**: Does the installer verify downloads?
4. **Offline installation**: Can we cache the binary for air-gapped builds?
5. **ARM64 support**: Does native installer support Linux aarch64? (For M1/M2 Mac users)

## References

- [Claude Code Getting Started](https://code.claude.com/docs/en/getting-started)
- [Claude Code npm package](https://www.npmjs.com/package/@anthropic-ai/claude-code)
- [Anthropic Documentation](https://docs.anthropic.com/en/docs/claude-code)
