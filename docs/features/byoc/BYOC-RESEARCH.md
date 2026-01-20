# BYOC Research: Container Customization Solutions

This document summarizes research into existing solutions for customizing development container environments, with the goal of enabling paude users to bring their own container configurations for different project types.

## Problem Statement

The current paude container is built specifically for paude's own development needs (Node.js 22, git, basic tools). When users want to use paude with different project types (Python, Go, Rust, etc.), they need:
- Different base images
- Different language runtimes and tools
- Project-specific dependencies
- Custom build tools and utilities

We need a way for users to specify their container requirements while maintaining paude's security model.

## Existing Solutions Analyzed

### 1. Dev Containers (devcontainer.json)

**Overview**: Industry standard for containerized development environments, created by Microsoft and now maintained as an open specification at [containers.dev](https://containers.dev/).

**How it works**:
- Configuration stored in `.devcontainer/devcontainer.json`
- Supports referencing existing images OR building custom Dockerfiles
- Features system for modular tool installation
- Lifecycle hooks (onCreateCommand, postCreateCommand, etc.)

**Key Properties**:
```json
{
  "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
  "build": {
    "dockerfile": "Dockerfile",
    "context": ".."
  },
  "features": {
    "ghcr.io/devcontainers/features/python:1": {},
    "ghcr.io/devcontainers/features/go:1": {"version": "1.21"}
  },
  "postCreateCommand": "pip install -r requirements.txt",
  "containerEnv": {
    "MY_VAR": "value"
  }
}
```

**Pros**:
- Industry standard (VS Code, GitHub Codespaces, JetBrains, Gitpod support)
- Huge ecosystem of pre-built features (~100+ official, many community)
- Well-documented specification
- Users may already have devcontainer.json in their repos
- Supports both simple image references and complex multi-stage builds

**Cons**:
- Full spec is complex (many properties we don't need)
- Some properties are IDE-specific (VS Code extensions, etc.)
- Features require internet access during build

**Sources**:
- [Dev Container Specification](https://containers.dev/implementors/json_reference/)
- [VS Code Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers)
- [Dev Container Features](https://github.com/devcontainers/features)

### 2. Gitpod Workspace Images

**Overview**: Gitpod provides workspace images with common tools pre-installed.

**How it works**:
- `.gitpod.yml` references an image or Dockerfile
- Base images available: `workspace-full` (all tools), `workspace-base` (minimal)
- Custom Dockerfile can extend base images

**Example**:
```yaml
image:
  file: .devcontainer/Dockerfile
```

**Pros**:
- Simple configuration
- Good base images with common tools

**Cons**:
- Gitpod-specific format
- Less ecosystem than devcontainers
- Requires specific user setup (gitpod user with specific UID)

**Sources**:
- [Gitpod Workspace Images](https://www.gitpod.io/docs/configure/workspaces/workspace-image)

### 3. Nix Flakes / Devbox

**Overview**: Reproducible development environments using Nix package manager.

**How it works**:
- `flake.nix` or `devbox.json` declares dependencies
- Nix handles installation with exact version pinning
- Works across platforms

**Example devbox.json**:
```json
{
  "packages": ["python@3.11", "nodejs@20", "go@1.21"]
}
```

**Pros**:
- Highly reproducible (lockfiles ensure exact versions)
- Works without containers
- Huge package repository (nixpkgs)

**Cons**:
- Steep learning curve
- Slower environment activation
- Doesn't provide container isolation (which is paude's core value)
- Different paradigm than containers

**Sources**:
- [NixOS Flakes](https://nixos.wiki/wiki/Flakes)
- [Devbox](https://www.jetify.com/devbox)

### 4. mise-en-place (formerly rtx)

**Overview**: Polyglot tool version manager, Rust rewrite of asdf.

**How it works**:
- `.mise.toml` or `.tool-versions` declares tool versions
- Automatically activates when entering directory
- Compatible with asdf plugins

**Example**:
```toml
[tools]
python = "3.11"
node = "20"
go = "1.21"
```

**Pros**:
- Simple, fast tool version management
- Zero-overhead (modifies PATH directly)
- Works with existing asdf ecosystem

**Cons**:
- Tool versions only, not full environment
- Requires tools to be installed inside container
- Not a complete container solution

**Sources**:
- [mise-en-place](https://mise.jdx.dev/)

### 5. Dagger

**Overview**: CI/CD pipelines as code, using containers.

**How it works**:
- SDK in multiple languages (Go, Python, TypeScript, etc.)
- Defines pipelines programmatically
- Runs in containers with caching

**Pros**:
- Powerful for complex workflows
- Good caching
- Local testing of pipelines

**Cons**:
- Overkill for simple dev environment setup
- Different use case (CI/CD, not dev environments)

**Sources**:
- [Dagger Docs](https://docs.dagger.io/)

### 6. AI Agent Container Isolation (Related Work)

**Overview**: Growing ecosystem of solutions for running AI coding agents in isolated containers.

**Key approaches**:
- DevContainer-based isolation for Cursor/Claude Code
- Network firewall controls via iptables
- Docker-based sandboxing with explicit mounts

**paude fits here**: paude already provides network isolation via Squid proxy and filesystem isolation via explicit mounts. The research confirms this is the right approach.

**Sources**:
- [Running AI Agents in DevContainers](https://codewithandrea.com/articles/run-ai-agents-inside-devcontainer/)
- [Sandboxing AI Coding Agents](https://mfyz.com/ai-coding-agent-sandbox-container/)

## Recommendation: Dev Containers

Based on this research, **Dev Containers (devcontainer.json)** is the clear winner for paude's BYOC feature:

### Why Dev Containers?

1. **Industry Standard**: Users may already have devcontainer.json in their repos
2. **Ecosystem**: Huge library of features for common tools
3. **Flexibility**: Supports simple image references through complex builds
4. **Compatible with paude's model**: We can layer our security controls on top
5. **Progressive complexity**: Simple projects use simple configs; complex projects can customize fully

### What We Should Support

**Minimum Viable Implementation**:
- `image` property: Use a pre-built container image
- `build.dockerfile` property: Build from a Dockerfile
- `features` property: Install dev container features

**Phase 2 Additions**:
- `postCreateCommand`: Run setup after container creation
- `containerEnv`: Set environment variables
- `build.args`: Pass build arguments

**Explicitly Not Supported** (IDE-specific or not relevant):
- `customizations.vscode.*`: VS Code extensions
- `forwardPorts`: Port forwarding (paude is CLI-based)
- `mounts`: Custom mounts (security concern - paude controls mounts)

### Alternative: Simple paude.json

If devcontainer.json feels too complex, we could define a simpler `paude.json`:

```json
{
  "image": "python:3.11-slim",
  "packages": ["git", "make", "gcc"],
  "setup": "pip install -r requirements.txt"
}
```

However, devcontainer.json compatibility provides more value since:
- Many projects already have it
- Rich ecosystem of features
- No new format to learn

## Implementation Approach

See BYOC-PLAN.md for the detailed implementation plan based on this research.

## References

- [Dev Container Specification](https://containers.dev/)
- [Dev Container Features](https://github.com/devcontainers/features)
- [VS Code Dev Containers](https://code.visualstudio.com/docs/devcontainers/containers)
- [GitHub Codespaces Dev Containers](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration/introduction-to-dev-containers)
- [Gitpod Workspace Images](https://www.gitpod.io/docs/configure/workspaces/workspace-image)
- [Devbox](https://www.jetify.com/devbox)
- [mise-en-place](https://mise.jdx.dev/)
