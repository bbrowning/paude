# Paude

A container runtime for [Claude Code](https://claude.ai/code) that provides isolated, secure execution with Google Vertex AI authentication. Supports local execution via Podman or remote execution via OpenShift/Kubernetes.

## Features

- Runs Claude Code in an isolated container
- Authenticates via Google Vertex AI (gcloud Application Default Credentials)
- Read-write access to current working directory only
- Git read operations work (clone, pull, local commits) - push blocked by design
- **Persistent sessions**: Survive restarts with named volumes/PVCs
- **Unified session management**: Same commands for both backends
- **Multiple backends**: Local (Podman) or remote (OpenShift/Kubernetes)

**Status**: Paude is a work-in-progress. See the [roadmap](docs/ROADMAP.md) for planned features and priorities.

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

# List all sessions
paude

# Create a session for the current workspace
paude create

# Create with YOLO mode (no permission prompts)
paude create --yolo

# Create with full network access (unrestricted)
paude create --allowed-domains all

# Create with specific domains allowed
paude create --allowed-domains pypi --allowed-domains .example.com

# Combine flags for full autonomous mode with network access
paude create --yolo --allowed-domains all

# Pass arguments to Claude Code
paude create -a '-p "explain this code"'
paude create --yolo -a '-p "refactor this function"'

# Force rebuild after changing config
paude create --rebuild

# Verify configuration without creating session
paude create --dry-run

# Start and connect to a session
paude start

# Verbose output (shows rsync progress, etc.)
paude start -v
```

On first run, paude pulls the base container image and installs Claude Code locally. This one-time setup takes a few minutes. Subsequent runs use the cached image and start immediately.

## Session Management

Paude provides persistent sessions that survive container/pod restarts with consistent commands across both Podman and OpenShift backends.

### Persistent Sessions

```bash
# Create a named session (without starting)
paude create my-project

# Start the session (launches container, connects)
paude start my-project

# Work in Claude... then detach with Ctrl+b d

# Reconnect later
paude connect my-project

# Stop to save resources (preserves state)
paude stop my-project

# Restart - instant resume, no reinstall
paude start my-project

# List all sessions
paude list

# Delete session completely
paude delete my-project --confirm
```

### Backend Selection

All session commands work with both backends:

```bash
# Explicit backend selection
paude create my-project --backend=openshift
paude list --backend=podman

# Backend-specific options
paude create my-project --backend=openshift \
  --pvc-size=50Gi \
  --storage-class=fast-ssd
```

### Session Lifecycle

| Command | What It Does |
|---------|--------------|
| `create` | Creates session resources (container/StatefulSet, volume/PVC) |
| `start` | Starts container/pod and connects |
| `stop` | Stops container/pod, preserves volume |
| `connect` | Attaches to running session |
| `remote` | Manages git remotes for code sync |
| `delete` | Removes all resources including volume |

### Code Synchronization

Sessions use git for code synchronization. Use `paude remote` to set up git remotes:

```bash
# Terminal 1: Create and start a session
paude create my-project
paude start my-project           # Stays attached to container

# Terminal 2: Set up remote and push code (while container is running)
paude remote add --push my-project  # Init git in container + push

# In container (Terminal 1): Install dependencies manually
pip install -e .                 # Or your preferred install command

# Later: Push more changes
git push paude-my-project main

# After Claude makes changes, pull them locally
git pull paude-my-project main
```

The `paude remote add` command:
1. Checks that the container is running (required)
2. Initializes a git repository in the container's workspace
3. Adds a git remote using the `ext::` protocol
4. Optionally pushes current branch with `--push`

## OpenShift Backend

For remote execution on OpenShift/Kubernetes clusters:

```bash
paude create --backend=openshift
paude start                       # In one terminal
paude remote add --push           # In another terminal (while running)
paude connect
```

The OpenShift backend provides:
- **Persistent sessions** using StatefulSets with PVC storage
- **Survive network disconnects** via tmux attachment
- **Git-based sync** via `paude remote` and git push/pull
- **Full config sync** including plugins and CLAUDE.md from `~/.claude/`
- **Automatic image push** to OpenShift internal registry

See [docs/OPENSHIFT.md](docs/OPENSHIFT.md) for detailed setup and usage.

## Workflow: Research vs Execution

Paude encourages separating research from execution for security:

**Execution mode** (default): `paude create`
- Network filtered via proxy - only Vertex AI and PyPI domains accessible
- Claude Code API calls work, but arbitrary exfiltration blocked
- Claude prompts for confirmation before edits and commands

**Autonomous mode**: `paude create --yolo`
- Same network filtering as execution mode
- Claude edits files and runs commands without confirmation prompts
- Passes `--dangerously-skip-permissions` to Claude Code inside the container
- Your host machine's Claude environment is unaffected (container isolation)

**Research mode**: `paude create --allowed-domains all`
- Full network access for web searches, documentation, package installation
- Treat outputs more carefully (prompt injection via web content is possible)
- A warning is displayed when network access is unrestricted

**Custom domains**: `paude create --allowed-domains default --allowed-domains .example.com`
- Add specific domains by combining with `default`
- Specifying domains without `default` replaces the allowlist entirely
- Special values: `all` (unrestricted), `default`, `vertexai`, `pypi`

This separation makes trust boundaries explicit. Do your research in one session, then execute changes in an isolated session.

## Network Architecture

By default, paude runs a proxy sidecar that filters network access:

```
┌─────────────────────────────────────────────────────┐
│  paude-internal network (no direct internet)        │
│  ┌───────────┐      ┌─────────────────────────────┐ │
│  │  Claude   │─────▶│  Proxy (squid allowlist)    │─┼──▶ *.googleapis.com
│  │ Container │      │                             │ │    *.pypi.org
│  └───────────┘      └─────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

The default allowlist includes:
- **vertexai**: Vertex AI and Google OAuth domains (`.googleapis.com`, `.google.com`)
- **pypi**: Python package repositories (`.pypi.org`, `.pythonhosted.org`)

Use `--allowed-domains` to customize:
```bash
# Add custom domain to defaults (must include 'default')
paude create --allowed-domains default --allowed-domains .example.com

# Use only vertexai (replaces default)
paude create --allowed-domains vertexai

# Disable proxy entirely (unrestricted)
paude create --allowed-domains all
```

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

## Python Virtual Environments

Paude automatically detects Python virtual environment directories (`.venv`, `venv`, `.virtualenv`, `env`, `.env`) and shadows them with empty tmpfs mounts. This allows you to:

- Use your host venv on your Mac
- Create a separate container venv inside paude
- Share source code between both

### How It Works

```
Host (.venv exists):         Container (.venv is empty tmpfs):
~/project/.venv/             ~/project/.venv/  <- empty, create new venv here
~/project/src/       <---->  ~/project/src/    <- shared
```

When a venv is detected, you'll see: `Shadowing venv: .venv`

### Automatic Setup

Add to your `paude.json` to auto-create the venv:

```json
{
  "setup": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

Or with uv for faster setup:

```json
{
  "setup": "uv venv && uv pip install -r requirements.txt"
}
```

### Configuration

Venv isolation is controlled via the `venv` field in `paude.json`:

```json
{"venv": "auto"}              // Default: auto-detect and shadow
{"venv": "none"}              // Disable: share venvs (will be broken)
{"venv": [".venv", "my-env"]} // Manual: specific directories to shadow
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
| Network | proxy-filtered (Vertex AI only) | Prevents data exfiltration |
| Current directory | read-write | Working files |
| `~/.config/gcloud` | read-only | Vertex AI auth |
| `~/.claude` | copied in, not mounted | Prevents host config poisoning |
| `~/.gitconfig` | read-only | Git identity |
| `~/.config/git/ignore` | read-only | Global gitignore patterns |
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
paude create --yolo

# DANGEROUS: Full network access, can send files anywhere
paude create --yolo --allowed-domains all
```

The `--yolo` flag enables autonomous execution (no confirmation prompts). This is safe when network filtering is active because Claude cannot exfiltrate files or secrets even if it reads them.

**Do not combine `--yolo` with `--allowed-domains all`** unless you fully trust the task. The combination allows Claude to read any file in your workspace and send it to arbitrary URLs.

### Residual Risks

These risks are accepted by design:

1. **Workspace destruction**: Claude can delete files including `.git`. Mitigation: push to remote before autonomous sessions.
2. **Secrets readable**: `.env` files in workspace are readable. Mitigation: network filtering prevents exfiltration; don't use `--allowed-domains all` with sensitive workspaces.
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

**devcontainer.json properties:**

| Property | Description |
|----------|-------------|
| `image` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `build.args` | Build arguments for Dockerfile |
| `features` | Dev container features (ghcr.io OCI artifacts) |
| `postCreateCommand` | Run after first start |
| `containerEnv` | Environment variables |

**paude.json properties:**

| Property | Description |
|----------|-------------|
| `base` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `build.args` | Build arguments for Dockerfile |
| `packages` | Additional system packages to install |
| `setup` | Run after first start |
| `venv` | Venv isolation: `"auto"`, `"none"`, or list of directories |

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
paude create --rebuild
```

### Verifying Configuration

Use `--dry-run` to verify your configuration without building or running anything:

```bash
paude create --dry-run
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
