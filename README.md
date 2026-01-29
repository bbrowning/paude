# Paude

Run Claude Code autonomously in a container. Claude makes commits, you pull them back.

## Why Paude?

- **Isolated execution**: Claude Code runs in a container, not on your host machine
- **Safe autonomous mode**: Enable `--yolo` without fear—Claude can't send your code anywhere
- **Git-based workflow**: Claude commits inside the container, you `git pull` the changes
- **Run anywhere**: Locally with Podman or remotely on OpenShift

## Demo

_Video walkthrough coming soon. Follow the Quick Start below to try it yourself._

## Quick Start

### Prerequisites

**For local (Podman)**:
- [Podman](https://podman.io/getting-started/installation) installed

**For remote (OpenShift)**:
- OpenShift CLI (`oc`) and cluster access

**Both require**:
- Google Cloud SDK with `gcloud auth application-default login`
- Environment variables (find your project ID in [Google Cloud Console](https://console.cloud.google.com)):
  ```bash
  export CLAUDE_CODE_USE_VERTEX=1
  export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
  export GOOGLE_CLOUD_PROJECT=your-project-id
  ```

### Install

```bash
pip install paude
```

> **First run**: Paude pulls container images on first use. This takes a few minutes; subsequent runs start immediately.

### The Workflow (Podman)

You'll use two terminals: one stays connected to Claude (interactive session), the other manages code sync via git.

```bash
# Terminal 1: Create and start session
cd your-project
paude create --yolo my-project
paude start my-project          # Opens tmux with Claude Code

# Terminal 2: Push your code (while Terminal 1 is running)
paude remote add --push my-project

# Claude works autonomously in Terminal 1...
# When ready, pull Claude's commits (use your branch name):
git pull paude-my-project main
```

**You'll know it's working when**: Terminal 1 shows the Claude Code interface, and `git pull` brings back commits that Claude made.

### The Workflow (OpenShift)

Same two-terminal approach, but runs on your cluster instead of locally.

```bash
# Terminal 1: Create and start on OpenShift
cd your-project
paude create --yolo --backend=openshift my-project
paude start my-project          # Opens tmux with Claude Code

# Terminal 2: Push your code (while pod is running)
paude remote add --push my-project

# Pull Claude's commits (use your branch name):
git pull paude-my-project main
```

### Passing a Task to Claude

Give Claude a specific task using the `-a` flag:

```bash
paude create --yolo my-project -a '-p "refactor the auth module"'
```

Or just start the session and type your request in the Claude Code interface.

### Something Not Working?

- Run `paude --help` for all options and examples
- Run `paude list` to check session status
- Use `paude create --dry-run` to verify configuration
- Use `paude start -v` for verbose output (shows sync progress)
- Check that your gcloud credentials are valid: `gcloud auth application-default print-access-token`

---

**Next steps**:
- Customize your environment → [Configuration](#configuration)
- Understand the security model → [Security Model](#security-model)
- Run on OpenShift → [OpenShift Backend](#openshift-backend)

## How It Works

```
Your Machine                    Container
    │                              │
    ├── git push ────────────────▶ │  Claude works here
    │                              │  (network-filtered)
    ◀── git pull ─────────────────┤
    │                              │
```

- **Git is the sync mechanism**—your local files stay untouched until you pull
- **`--yolo` is safe** because network filtering blocks Claude from sending data to arbitrary URLs
- Claude can only reach Vertex AI (for the API) and PyPI (for packages) by default

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

- Python 3.11+ (for the Python package)
- [Podman](https://podman.io/getting-started/installation) (for local backend)
- OpenShift CLI `oc` (for OpenShift backend)
- Google Cloud SDK configured (`gcloud auth application-default login`)

### macOS Setup

On macOS, Podman runs in a Linux VM that only mounts `/Users` by default. If your working directory is outside `/Users` (e.g., on a separate volume), configure the Podman machine:

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

## Session Management

Paude provides persistent sessions that survive container/pod restarts.

```bash
# Quick start: create session for current directory (uses directory name)
paude create
paude start

# List all sessions (shorthand: just `paude`)
paude list
paude
```

### Commands

| Command | What It Does |
|---------|--------------|
| `create` | Creates session resources (container/StatefulSet, volume/PVC) |
| `start` | Starts container/pod and connects |
| `stop` | Stops container/pod, preserves volume |
| `connect` | Attaches to running session |
| `remote` | Manages git remotes for code sync |
| `delete` | Removes all resources including volume |
| `list` | Shows all sessions |

### Examples

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

# Delete session completely
paude delete my-project --confirm
```

### Backend Selection

```bash
# Explicit backend selection
paude create my-project --backend=openshift
paude list --backend=podman

# Backend-specific options
paude create my-project --backend=openshift \
  --pvc-size=50Gi \
  --storage-class=fast-ssd
```

## Code Synchronization

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

## Configuration

### Network Domains

By default, paude runs a proxy sidecar that filters network access to Vertex AI and PyPI only.

```
┌─────────────────────────────────────────────────────────┐
│  paude-internal network (no direct internet)            │
│  ┌───────────┐        ┌───────────────────────────────┐ │
│  │  Claude   │───────▶│  Proxy (squid allowlist)      │─┼──▶ *.googleapis.com
│  │ Container │        │                               │ │    *.pypi.org
│  └───────────┘        └───────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

```bash
# Add custom domain to defaults (must include 'default')
paude create --allowed-domains default --allowed-domains .example.com

# Full network access (unrestricted) - use with caution
paude create --allowed-domains all

# Use only vertexai (replaces default)
paude create --allowed-domains vertexai
```

The default allowlist includes:
- **vertexai**: Vertex AI and Google OAuth domains (`.googleapis.com`, `.google.com`)
- **pypi**: Python package repositories (`.pypi.org`, `.pythonhosted.org`)

**Special values**: `all` (unrestricted), `default` (vertexai + pypi), `vertexai`, `pypi`. Specifying domains without `default` replaces the allowlist entirely.

### Workflow Modes

**Execution mode** (default): `paude create`
- Network filtered via proxy
- Claude prompts for confirmation before edits and commands

**Autonomous mode**: `paude create --yolo`
- Same network filtering
- Claude edits files and runs commands without confirmation prompts
- Passes `--dangerously-skip-permissions` to Claude Code

**Research mode**: `paude create --allowed-domains all`
- Full network access for web searches, documentation
- Treat outputs more carefully (prompt injection via web content is possible)

### Custom Container Environments (BYOC)

Paude supports custom container configurations via devcontainer.json or paude.json.

**Using paude.json** (simpler):

```json
{
    "base": "python:3.11-slim",
    "packages": ["make", "gcc"],
    "setup": "pip install -r requirements.txt"
}
```

**Using devcontainer.json**:

```json
{
    "image": "python:3.11-slim",
    "postCreateCommand": "pip install -r requirements.txt"
}
```

See [`examples/README.md`](examples/README.md) for more configurations (Python, Node.js, Go).

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

### Python Virtual Environments

Paude automatically detects Python venv directories (`.venv`, `venv`, etc.) and shadows them with empty tmpfs mounts. This allows you to:

- Use your host venv on your Mac
- Create a separate container venv inside paude
- Share source code between both

Add to your `paude.json` to auto-create the venv:

```json
{
  "setup": "python -m venv .venv && .venv/bin/pip install -r requirements.txt"
}
```

Configuration via the `venv` field:

```json
{"venv": "auto"}              // Default: auto-detect and shadow
{"venv": "none"}              // Disable: share venvs (will be broken)
{"venv": [".venv", "my-env"]} // Manual: specific directories to shadow
```

### Verifying Configuration

```bash
# Verify configuration without building or running
paude create --dry-run

# Force rebuild after changing config
paude create --rebuild
```

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

**Do not combine `--yolo` with `--allowed-domains all`** unless you fully trust the task.

### Workspace Protection

The container has full read-write access to your working directory. **Your protection is git itself.** Push important work to a remote before running in autonomous mode:

```bash
git push origin main
```

If something goes wrong, recovery is a clone away.

### Residual Risks

These risks are accepted by design:

1. **Workspace destruction**: Claude can delete files including `.git`. Mitigation: push to remote before autonomous sessions.
2. **Secrets readable**: `.env` files in workspace are readable. Mitigation: network filtering prevents exfiltration; don't use `--allowed-domains all` with sensitive workspaces.
3. **No audit logging**: Commands executed aren't logged. This is a forensics gap, not a security breach vector.

### Unsupported devcontainer Properties (Security)

These properties are ignored for security reasons:
- `mounts` - paude controls mounts
- `runArgs` - paude controls run arguments
- `privileged` - never allowed
- `capAdd` - never allowed
- `forwardPorts` - paude controls networking
- `remoteUser` - paude controls user

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and release instructions.

**Status**: Paude is a work-in-progress. See the [roadmap](docs/ROADMAP.md) for planned features.

## License

MIT
