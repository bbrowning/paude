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
- `~/.claude` → `/tmp/claude.seed` (ro) - copied into container on startup
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
# Rebuild image after Dockerfile changes
podman rmi paude:latest
./paude

# Test basic functionality
./paude --version
./paude --help
```

## macOS Considerations

Paths outside `/Users/` require Podman machine configuration. The script detects this and provides guidance when volume mounts fail.

## Security Hardening In Progress

**Status**: Actively hardening paude security before enabling autonomous execution.

**Primary Document**: See `SECURITY-TASKS.md` for complete task list, priorities, and progress.

### Current Focus
Working through CRITICAL priority tasks to prevent:
- Network exfiltration of files/secrets
- Workspace filesystem destruction
- Unauthorized git push operations

### Workflow for Security Tasks

**Starting a Security Session**:
1. Read `SECURITY-TASKS.md` to see current progress
2. Pick next PENDING task in priority order (start with CRITICAL)
3. Create branch: `git checkout -b security/task-N-description`

**Working on a Task**:
1. Update task status to IN PROGRESS in `SECURITY-TASKS.md`
2. Read the threat description and mitigation options
3. Implement the chosen mitigation
4. Run the testing plan to verify it works
5. Update task status to COMPLETED with implementation notes
6. Commit: `git commit -m "Security: Complete Task N - [description]"`
7. Merge to main

**After Completing CRITICAL Tasks**:
- Evaluate if comfortable with semi-autonomous execution
- HIGH and MEDIUM tasks provide defense-in-depth
- Document any residual risk acceptance

### Key Security Principles
- Work on one task at a time
- Test thoroughly before marking complete
- Commit after each completed task (git history = recovery mechanism)
- Update progress summary in SECURITY-TASKS.md
- Don't skip tasks - they're prioritized by risk severity
