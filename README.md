# Paude

A Podman wrapper that runs [Claude Code](https://claude.ai/code) inside a container for isolated, secure usage with Google Vertex AI authentication.

## Features

- Runs Claude Code in an isolated container
- Authenticates via Google Vertex AI (gcloud Application Default Credentials)
- Read-write access to current working directory only
- Git read operations work (clone, pull, local commits) - push blocked by design
- Persists Claude Code settings between sessions

## Requirements

- [Podman](https://podman.io/getting-started/installation) installed
- Google Cloud SDK configured (`gcloud auth application-default login`)
- Vertex AI environment variables set:
  ```bash
  export CLAUDE_CODE_USE_VERTEX=1
  export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
  export GOOGLE_CLOUD_PROJECT=your-project-id
  ```

## Usage

```bash
# Run Claude Code interactively (network filtered to Vertex AI only)
./paude

# Enable full network access for web searches and package installation
./paude --allow-network

# Enable autonomous mode (no confirmation prompts for edits/commands)
./paude --yolo

# Combine flags for full autonomous mode with network access
./paude --yolo --allow-network

# Pass arguments to Claude Code
./paude --help
./paude -p "explain this code"
```

The container images are built automatically on first run.

## Workflow: Research vs Execution

Paude encourages separating research from execution for security:

**Execution mode** (default): `./paude`
- Network filtered via proxy - only Google/Vertex AI domains accessible
- Claude Code API calls work, but arbitrary exfiltration blocked
- Claude prompts for confirmation before edits and commands

**Autonomous mode**: `./paude --yolo`
- Same network filtering as execution mode
- Claude edits files and runs commands without confirmation prompts
- Passes `--dangerously-skip-permissions` to Claude Code inside the container
- Your host machine's Claude environment is unaffected (container isolation)

**Research mode**: `./paude --allow-network`
- Full network access for web searches, documentation, package installation
- Treat outputs more carefully (prompt injection via web content is possible)
- A warning is displayed when network access is enabled

This separation makes trust boundaries explicit. Do your research in one session, then execute changes in an isolated session.

## Network Architecture

By default, paude runs a proxy sidecar that filters network access:

```
┌─────────────────────────────────────────────────────┐
│  paude-internal network (no direct internet)        │
│  ┌───────────┐      ┌─────────────────────────────┐ │
│  │  Claude   │─────▶│  Proxy (squid allowlist)    │─┼──▶ *.googleapis.com
│  │ Container │      │                             │ │    *.google.com
│  └───────────┘      └─────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

The allowlist (`proxy/squid.conf`) permits only domains required for Vertex AI authentication and API calls. Edit this file to add additional allowed domains if needed.

## macOS Setup

On macOS, Podman runs in a Linux VM that only mounts `/Users` by default. If your working directory is outside `/Users` (e.g., on a separate volume), you need to configure the Podman machine:

```bash
podman machine stop
podman machine rm
podman machine init \
  --volume /Users:/Users \
  --volume /private:/private \
  --volume /var/folders:/var/folders \
  --volume /Volumes/YourVolume:/Volumes/YourVolume
podman machine start
```

## Workspace Protection

The container has full read-write access to your working directory. This means Claude Code can create, modify, or delete any files in your project, including the `.git` directory.

**Your protection is git itself.** Push important work to a remote before running in autonomous mode (`--yolo`):

```bash
# Before autonomous sessions, push your work
git push origin main

# Your remote can be GitHub, GitLab, or a local bare repo
git clone --bare . /backup/myproject.git
git remote add backup /backup/myproject.git
git push backup main
```

If something goes wrong, recovery is a clone away. This matches how git is designed to work - every remote is a complete backup.

## Security Model

The container intentionally restricts certain operations:

| Resource | Access | Purpose |
|----------|--------|---------|
| Network | proxy-filtered (Google/Vertex only) | Prevents data exfiltration |
| Current directory | read-write | Working files |
| `~/.config/gcloud` | read-only | Vertex AI auth |
| `~/.claude` | read-write | Claude Code config |
| `~/.gitconfig` | read-only | Git identity |
| SSH keys | not mounted | Prevents git push via SSH |
| GitHub CLI config | not mounted | Prevents gh operations |

## License

MIT
