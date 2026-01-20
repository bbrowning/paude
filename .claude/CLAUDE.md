# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a Podman wrapper that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication.

## Architecture

The project consists of a main script and container definitions:

- `paude` - Bash script that validates environment, builds the container image if needed, and runs Claude Code with proper volume mounts and environment variables
- `containers/paude/` - Main container artifacts (Dockerfile, entrypoint.sh) for Claude Code (Node.js 22 slim + git + Claude Code as non-root user)
- `containers/proxy/` - Proxy container artifacts (Dockerfile, entrypoint.sh, squid.conf) for network filtering

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

```bash
# Rebuild images after container changes
make clean
make run

# Test basic functionality
PAUDE_DEV=1 ./paude --version
PAUDE_DEV=1 ./paude --help
```

## Documentation Requirements

When adding or changing user-facing features (flags, options, behavior):
1. Update `README.md` with the new usage patterns
2. Update the `show_help()` function in `paude` if adding new flags
3. Keep examples consistent between README and help output

## macOS Considerations

Paths outside `/Users/` require Podman machine configuration. The script detects this and provides guidance when volume mounts fail.

