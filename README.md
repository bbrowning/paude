# Paude

Run AI coding agents in secure containers. They make commits, you pull them back.

## Supported Agents

| Agent | Flag | Status |
|-------|------|--------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `--agent claude` (default) | Supported |
| [Cursor CLI](https://docs.cursor.com/cli) | `--agent cursor` | Supported |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `--agent gemini` | Supported |
| [OpenClaw](https://github.com/openclaw/openclaw) | `--agent openclaw` | Supported |

> Agents are installed automatically inside the container — no local agent installation needed. You just need authentication credentials for your chosen provider.

## Why Paude?

- **Isolated execution**: Your agent runs in a container, not on your host machine
- **Safe autonomous mode**: Enable `--yolo` without fear — the agent can't send your code anywhere
- **Git-based workflow**: The agent commits inside the container, you `git pull` the changes
- **Run anywhere**: Locally with Podman or Docker, remotely via SSH, or on OpenShift

## Demo

[![asciicast](https://asciinema.org/a/7bh955pH5e8YPbyl.svg)](https://asciinema.org/a/7bh955pH5e8YPbyl)

> The demo shows Claude Code, but the workflow is identical with other agents.

## Quick Start

### Prerequisites

**Container runtime**: [Podman](https://podman.io/getting-started/installation) or [Docker](https://docs.docker.com/get-docker/) (for local backend).

**Authentication** — set up credentials for your chosen provider:

<details>
<summary><strong>Google Cloud / Vertex AI</strong> (Claude Code, Gemini CLI, OpenClaw)</summary>

Install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install), then:

```bash
gcloud auth application-default login
```

Set your project (find the ID in [Google Cloud Console](https://console.cloud.google.com)):

```bash
# Claude Code via Vertex
export CLAUDE_CODE_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
export GOOGLE_CLOUD_PROJECT=your-project-id

# Gemini CLI / OpenClaw via Vertex
export GOOGLE_CLOUD_PROJECT=your-project-id
```

</details>

<details>
<summary><strong>Anthropic API key</strong> (Claude Code, OpenClaw)</summary>

```bash
export ANTHROPIC_API_KEY=your-api-key
```

For OpenClaw, also pass `--provider anthropic`:

```bash
paude create --agent openclaw --provider anthropic ...
```

</details>

<details>
<summary><strong>OpenAI API key</strong> (OpenClaw)</summary>

```bash
export OPENAI_API_KEY=your-api-key
paude create --agent openclaw --provider openai ...
```

</details>

<details>
<summary><strong>Cursor</strong></summary>

```bash
agent login  # or set CURSOR_API_KEY=your-api-key
```

> **macOS note**: On Mac hosts, `CURSOR_API_KEY` is the simplest authentication method. Without it, each paude session requires a separate browser-based OAuth login via `agent login` inside the container.

</details>

### Install

```bash
uv tool install paude
```

> **First run**: Paude pulls container images on first use. This takes a few minutes; subsequent runs start immediately.

### Your First Session

```bash
# OpenClaw — browser-based, no local agent install needed
# Web UI opens at http://localhost:18789
paude create --agent openclaw --allowed-domains "default openclaw" my-project

# Claude Code (default)
cd your-project
paude create --yolo --git my-project

# Cursor CLI
paude create --agent cursor --yolo --git my-project

# Gemini CLI
paude create --agent gemini --yolo --git my-project

# Connect to a CLI agent's running session
paude connect my-project

# Pull the agent's commits (use your branch name):
git pull paude-my-project main
```

**You'll know it's working when**: For CLI agents, `paude connect` shows the agent interface and `git pull` brings back commits. For OpenClaw, open `http://localhost:18789` in your browser.

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
- Check credentials: `gcloud auth application-default print-access-token` (Vertex/Gemini) or verify your API key is exported

---

**Learn more**:
- [Session Management](docs/SESSIONS.md) — commands, lifecycle, code sync
- [Configuration](docs/CONFIGURATION.md) — defaults, network domains, GitHub CLI, custom environments
- [Security Model](docs/SECURITY.md) — attack vectors, `--yolo` safety, residual risks
- [Orchestration](docs/ORCHESTRATION.md) — fire-and-forget workflow, harvest, PRs
- [Remote Hosts & Docker](docs/REMOTE.md) — SSH remotes, Docker backend, GPU passthrough
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
- [Podman](https://podman.io/getting-started/installation) or [Docker](https://docs.docker.com/get-docker/) (for local backend)
- OpenShift CLI `oc` (for OpenShift backend)
- Auth credentials for your provider (Google Cloud SDK, API key, etc.)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and release instructions.

## License

MIT
