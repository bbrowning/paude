# Paude

Run AI coding agents in secure containers. They make commits, you pull them back.

## Supported Agents

| Agent | Flag | Status |
|-------|------|--------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `--agent claude` (default) | Supported |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `--agent gemini` | Supported |

> **Note**: Your chosen agent must be installed and working on your local machine first.

## Why Paude?

- **Isolated execution**: Your agent runs in a container, not on your host machine
- **Safe autonomous mode**: Enable `--yolo` without fear — the agent can't send your code anywhere
- **Git-based workflow**: The agent commits inside the container, you `git pull` the changes
- **Run anywhere**: Locally with Podman or remotely on OpenShift

## Demo

[![asciicast](https://asciinema.org/a/7bh955pH5e8YPbyl.svg)](https://asciinema.org/a/7bh955pH5e8YPbyl)

> The demo shows Claude Code, but the workflow is identical with other agents.

## Quick Start

### Prerequisites

**Your agent**: Claude Code or Gemini CLI installed and working locally.

**Podman**: [Install Podman](https://podman.io/getting-started/installation) (for local backend).

**Google Cloud SDK**: `gcloud auth application-default login`

**Environment variables** (find your project ID in [Google Cloud Console](https://console.cloud.google.com)):

Claude Code:
```bash
export CLAUDE_CODE_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
export GOOGLE_CLOUD_PROJECT=your-project-id
```

Gemini CLI:
```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
```

### Install

```bash
uv tool install paude
```

> **First run**: Paude pulls container images on first use. This takes a few minutes; subsequent runs start immediately.

### Your First Session

```bash
# Claude Code (default)
cd your-project
paude create --yolo --git my-project

# Gemini CLI
paude create --agent gemini --yolo --git my-project

# Connect to the running session
paude connect my-project

# Pull the agent's commits (use your branch name):
git pull paude-my-project main
```

**You'll know it's working when**: `paude connect` shows the agent interface, and `git pull` brings back commits the agent made.

### Passing a Task

```bash
paude create --yolo my-project -a '-p "refactor the auth module"'
```

Or just start the session and type your request in the agent interface.

### Something Not Working?

- Run `paude --help` for all options and examples
- Run `paude list` to check session status
- Use `paude create --dry-run` to verify configuration
- Use `paude start -v` for verbose output (shows sync progress)
- Check that your gcloud credentials are valid: `gcloud auth application-default print-access-token`

---

**Learn more**:
- [Session Management](docs/SESSIONS.md) — commands, lifecycle, code sync
- [Configuration](docs/CONFIGURATION.md) — network domains, GitHub CLI, custom environments
- [Security Model](docs/SECURITY.md) — attack vectors, `--yolo` safety, residual risks
- [Orchestration](docs/ORCHESTRATION.md) — fire-and-forget workflow, harvest, PRs
- [OpenShift Backend](docs/OPENSHIFT.md) — remote execution on Kubernetes

## How It Works

```
Your Machine                    Container
    |                              |
    |-- git push ----------------▶ |  Agent works here
    |                              |  (network-filtered)
    ◀-- git pull -----------------|
    |                              |
```

- **Git is the sync mechanism** — your local files stay untouched until you pull
- **`--yolo` is safe** because network filtering blocks the agent from sending data to arbitrary URLs
- The agent can only reach its API (e.g., Vertex AI) and package registries (e.g., PyPI) by default

## Install from Source

```bash
git clone https://github.com/bbrowning/paude
cd paude
uv venv --python 3.12 --seed
source .venv/bin/activate
pip install -e .
```

### Requirements

- Python 3.11+ (for the Python package)
- Your chosen agent CLI installed locally (Claude Code or Gemini CLI)
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

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and release instructions.

## License

MIT
