# Paude

A Podman wrapper that runs [Claude Code](https://claude.ai/code) inside a container for isolated, secure usage with Google Vertex AI authentication.

## Features

- Runs Claude Code in an isolated container
- Authenticates via Google Vertex AI (gcloud Application Default Credentials)
- Read-write access to current working directory only
- Git read operations work (clone, pull, local commits) - push blocked by design
- Persists Claude Code settings between sessions

## Installation

### Using pip

```bash
pip install paude
```

### From source

```bash
git clone https://github.com/bbrowning/paude
cd paude
uv venv --python 3.12 --seed
source .venv/bin/activate
pip install -e .
```

### Bash script (legacy)

Download the bash script directly:

```bash
# Download the paude script
curl -LO https://github.com/bbrowning/paude/releases/latest/download/paude
chmod +x paude

# Move to a directory in your PATH (e.g., ~/.local/bin)
mv paude ~/.local/bin/

# Container images are pulled automatically on first run
```

### Requirements

- [Podman](https://podman.io/getting-started/installation) installed
- Python 3.11+ (for the Python package)
- Google Cloud SDK configured (`gcloud auth application-default login`)
- Vertex AI environment variables set:
  ```bash
  export CLAUDE_CODE_USE_VERTEX=1
  export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
  export GOOGLE_CLOUD_PROJECT=your-project-id
  ```

## Usage

```bash
# Show paude help (options, examples, security notes)
paude --help

# Run Claude Code interactively (network filtered to Vertex AI only)
paude

# Enable full network access for web searches and package installation
paude --allow-network

# Enable autonomous mode (no confirmation prompts for edits/commands)
paude --yolo

# Combine flags for full autonomous mode with network access
paude --yolo --allow-network

# Pass arguments to Claude Code (use -- separator)
paude -- --help
paude -- -p "explain this code"
paude --yolo -- -p "refactor this function"
```

Arguments before `--` are interpreted by paude. Arguments after `--` are passed directly to Claude Code.

Container images are pulled automatically on first run. For development, run from the cloned repo to build images locally.

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

The allowlist (`containers/proxy/squid.conf`) permits only domains required for Vertex AI authentication and API calls. Edit this file to add additional allowed domains if needed.

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
| `~/.claude` | copied in, not mounted | Prevents host config poisoning |
| `~/.gitconfig` | read-only | Git identity |
| SSH keys | not mounted | Prevents git push via SSH |
| GitHub CLI config | not mounted | Prevents gh operations |
| Git credentials | not mounted | Prevents HTTPS git push |

### Verified Attack Vectors

These exfiltration paths have been tested and confirmed blocked:

| Attack Vector | Status | How |
|--------------|--------|-----|
| HTTP/HTTPS exfiltration | Blocked | Internal network has no external DNS; proxy allowlists only Google domains |
| Git push via SSH | Blocked | No `~/.ssh` mounted; DNS resolution fails anyway |
| Git push via HTTPS | Blocked | No credential helpers; no stored credentials; DNS blocked |
| GitHub CLI operations | Blocked | `gh` command not installed in container |
| Modify cloud credentials | Blocked | gcloud directory mounted read-only |
| Escape container | Blocked | Non-root user; standard Podman isolation |

### When is `--yolo` Safe?

```bash
# SAFE: Network filtered, cannot exfiltrate data
./paude --yolo

# DANGEROUS: Full network access, can send files anywhere
./paude --yolo --allow-network
```

The `--yolo` flag enables autonomous execution (no confirmation prompts). This is safe when network filtering is active because Claude cannot exfiltrate files or secrets even if it reads them.

**Do not combine `--yolo` with `--allow-network`** unless you fully trust the task. The combination allows Claude to read any file in your workspace and send it to arbitrary URLs.

### Residual Risks

These risks are accepted by design:

1. **Workspace destruction**: Claude can delete files including `.git`. Mitigation: push to remote before autonomous sessions.
2. **Secrets readable**: `.env` files in workspace are readable. Mitigation: network filtering prevents exfiltration; don't use `--allow-network` with sensitive workspaces.
3. **No audit logging**: Commands executed aren't logged. This is a forensics gap, not a security breach vector.

## Custom Container Environments (BYOC)

Paude supports custom container configurations via devcontainer.json or paude.json. This allows you to use paude with any project type (Python, Go, Rust, etc.) while maintaining security guarantees.

### Using devcontainer.json

Create `.devcontainer/devcontainer.json` in your project:

```json
{
    "image": "python:3.11-slim",
    "postCreateCommand": "pip install -r requirements.txt"
}
```

Or with a custom Dockerfile:

```json
{
    "build": {
        "dockerfile": "Dockerfile",
        "context": ".."
    }
}
```

### Using paude.json (simpler)

Create `paude.json` at project root:

```json
{
    "base": "python:3.11-slim",
    "packages": ["make", "gcc"],
    "setup": "pip install -r requirements.txt"
}
```

### Supported Properties

| Property | Description |
|----------|-------------|
| `image` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `build.args` | Build arguments for Dockerfile |
| `features` | Dev container features (ghcr.io OCI artifacts) |
| `postCreateCommand` | Run after first start |
| `containerEnv` | Environment variables |

### Unsupported Properties (Security)

These properties are ignored for security reasons:
- `mounts` - paude controls mounts
- `runArgs` - paude controls run arguments
- `privileged` - never allowed
- `capAdd` - never allowed
- `forwardPorts` - paude controls networking
- `remoteUser` - paude controls user

### Caching and Rebuilding

Custom images are cached based on a hash of the configuration. To force a rebuild after changing your config:

```bash
paude --rebuild
```

### Verifying Configuration

Use `--dry-run` to verify your configuration without building or running anything:

```bash
paude --dry-run
```

This shows the detected configuration, base image, packages, and the Dockerfile that would be generated. Useful for debugging paude.json or devcontainer.json issues.

### Example Configurations

See [`examples/README.md`](examples/README.md) for detailed instructions on running paude with different container environments. Sample configurations include:

- `examples/python/` - Python 3.11 with pytest
- `examples/node/` - Node.js 20
- `examples/go/` - Go 1.21

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and release instructions.

## License

MIT
