# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Paude is a Podman wrapper that runs Claude Code inside a container for isolated, secure usage with Google Vertex AI authentication.

## Architecture

Two files comprise the entire project:

- `paude` - Bash script that validates environment, builds the container image if needed, and runs Claude Code with proper volume mounts and environment variables
- `Dockerfile` - Defines the container image (Node.js 22 slim + git + Claude Code as non-root user)

## Volume Mounts

The script mounts these paths from host to container:
- Current working directory at same path (rw) - preserves real paths for trust prompts
- `~/.config/gcloud` → `/home/paude/.config/gcloud` (ro) - Vertex AI credentials
- `~/.claude` → `/home/paude/.claude` (rw) - Claude Code config directory
- `~/.claude.json` → `/home/paude/.claude.json` (rw) - Claude Code legacy settings (text style, etc.)
- `~/.gitconfig` → `/home/paude/.gitconfig` (ro) - Git identity

## Security Model

- No SSH keys mounted - prevents `git push` via SSH
- No GitHub CLI config mounted - prevents `gh` operations
- gcloud credentials are read-only
- Non-root user inside container

## Testing Changes

```bash
# Rebuild image after Dockerfile changes
podman rmi paude:latest
./paude

# Test basic functionality
./paude --version
./paude --help
```

## macOS Considerations

Paths outside `/Users/` require Podman machine configuration. The script detects this and provides guidance when volume mounts fail.
