# Paude

Run AI coding agents in secure containers. They make commits, you pull them back.

## Supported Agents

| Agent | Flag | Status |
|-------|------|--------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | `--agent claude` (default) | Supported |
| [Codex CLI](https://github.com/openai/codex) | `--agent codex` | Supported |
| [Cursor CLI](https://docs.cursor.com/cli) | `--agent cursor` | Supported |
| [Gas City](https://github.com/gastownhall/gascity) | `--agent gascity` | Supported |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | `--agent gemini` | Supported |
| [OpenClaw](https://github.com/openclaw/openclaw) | `--agent openclaw` | Supported |

> Agents are installed automatically inside the container — no local agent installation needed. You just need authentication credentials for your chosen provider.

## Why Paude?

- **Isolated execution**: Your agent runs in a container, not on your host machine
- **Safe autonomous mode**: Enable `--yolo` without fear — the agent can't send your code anywhere
- **Git-based workflow**: The agent commits inside the container, you `git pull` the changes
- **Run anywhere**: Locally or over SSH with Podman or Docker

## Demo

[![asciicast](https://asciinema.org/a/7bh955pH5e8YPbyl.svg)](https://asciinema.org/a/7bh955pH5e8YPbyl)

> The demo shows Claude Code, but the workflow is identical with other agents.

## Quick Start

### Prerequisites

**Container runtime**: [Podman](https://podman.io/getting-started/installation) or [Docker](https://docs.docker.com/get-docker/), locally or on a remote host reached over SSH.

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
<summary><strong>OpenAI API key</strong> (Codex CLI, OpenClaw)</summary>

```bash
export OPENAI_API_KEY=your-api-key
paude create --agent openclaw --provider openai ...
```

</details>

<details>
<summary><strong>ChatGPT plan login</strong> (Codex CLI)</summary>

```bash
paude create --agent codex --yolo --git my-project
paude connect my-project
```

`chatgpt` is the default provider for the Codex agent and is only supported
on the local Podman/Docker backend (not `--backend openshift`, which needs
`--provider openai` instead — see below).

Inside the session, run `codex login`. Codex prints a URL and code — complete
the login in any browser, on any device (no localhost callback or port
forwarding is needed). The session's own proxy sidecar captures the resulting
OAuth tokens and manages refresh transparently and independently; real tokens
never reach the agent container. Each session has its own OAuth lineage tied
to its own private state volume, so multiple ChatGPT-plan sessions can run
concurrently without one session's token refresh invalidating another's.

Pass `--provider openai` to use the plain `OPENAI_API_KEY` provider instead
— no ChatGPT plan, no proxy-managed auth (this is the only provider Codex
supports on `--backend openshift`). The default Codex network policy allows
`chatgpt.com` and `auth.openai.com` for the ChatGPT API and OAuth exchange.
Paude selects Codex's HTTP/SSE transport by default because the local MITM
proxy does not support the Responses WebSocket transport. Codex Apps are
disabled for ChatGPT OAuth sessions so the Apps MCP integration does not
attempt to connect from the container; standalone MCP server configuration
is unchanged.

**Breaking change from previous versions**: paude no longer reads
`~/.codex/auth.json` from the host. Sessions created under the old
host-seeded model must be recreated (`paude delete` + `paude create --agent
codex`) and re-authenticated with `codex login` inside the container.

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
paude create --agent openclaw --allowed-domains "default openclaw" my-project

# Claude Code (default)
cd your-project
paude create --yolo --git my-project

# Codex CLI
paude create --agent codex --yolo --git my-project

# Cursor CLI
paude create --agent cursor --yolo --git my-project

# Gemini CLI
paude create --agent gemini --yolo --git my-project

# Connect to a CLI agent's running session
paude connect my-project

# Pull the agent's commits (use your branch name):
git pull paude-my-project main
```

**You'll know it's working when**: For CLI agents, `paude connect` shows the agent interface and `git pull` brings back commits. For OpenClaw, `paude connect` prints a URL — open it in your browser.

### OpenTelemetry Export

Export agent telemetry (metrics, logs, traces) to any OTLP-compatible collector:

```bash
paude create --otel-endpoint http://collector:4318 my-project
```

The endpoint hostname is automatically added to the proxy allowlist and non-standard ports (like 4318) are opened in the proxy. Supported agents: Claude Code, Gemini CLI, OpenClaw. Set `otel-endpoint` in `~/.config/paude/defaults.json` to apply globally.

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
- Auth credentials for your provider (Google Cloud SDK, API key, etc.)

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and release instructions.

## License

MIT
