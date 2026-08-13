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
| [OpenCode](https://opencode.ai) | `--agent opencode` | Supported |

> Agents are installed automatically inside the container — no local agent installation needed. You just need authentication credentials for your chosen provider.

Paude-managed agent containers include common development utilities, including
`rg` (ripgrep) for fast code search.
The Codex CLI image also includes Node.js for documentation tooling, using the
custom base image's package manager or an existing Node.js installation.

Agent installation, credential setup, and provider selection are independent.
`--agents` is the exact install set and its first entry launches as the primary.
`--providers` selects credentials to configure, while `--agent-provider` maps
installed agents to those providers:

```bash
paude create \
  --agents gascity,claude,codex \
  --providers vertex,chatgpt \
  --agent-provider gascity=vertex,claude=vertex,codex=chatgpt \
  my-project
```

Unmapped agents use their default provider. If `--providers` is omitted, its
value is derived from the effective mappings; when supplied, it must include
every mapped provider but may include extras. `--provider` remains shorthand
for mapping the primary agent. Gas City no longer installs child CLIs
implicitly—list every CLI the image should contain.

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
<summary><strong>Google Cloud / Vertex AI</strong> (Claude Code, Gemini CLI, OpenClaw, OpenCode)</summary>

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

# OpenCode via Vertex
export GOOGLE_CLOUD_PROJECT=your-project-id
export VERTEX_LOCATION=us-east5
```

</details>

<details>
<summary><strong>Anthropic API key</strong> (Claude Code, OpenClaw, OpenCode)</summary>

```bash
export ANTHROPIC_API_KEY=your-api-key
```

This is the default provider for OpenCode. For Claude Code and OpenClaw, also pass `--provider anthropic`:

```bash
paude create --agent openclaw --provider anthropic ...
```

</details>

<details>
<summary><strong>OpenAI API key</strong> (Codex CLI, OpenClaw, OpenCode)</summary>

```bash
export OPENAI_API_KEY=your-api-key
paude create --agent openclaw --provider openai ...
paude create --agent opencode --provider openai ...
```

</details>

<details>
<summary><strong>ChatGPT plan login</strong> (Codex CLI)</summary>

```bash
paude create --agent codex --yolo --git my-project
paude connect my-project
```

`chatgpt` is the default provider for the Codex agent.

Inside the session, run `codex login`. Codex prints a URL and code — complete
the login in any browser, on any device (no localhost callback or port
forwarding is needed). The session's own proxy sidecar captures the resulting
OAuth tokens and manages refresh transparently and independently; real tokens
never reach the agent container. Each session has its own OAuth lineage tied
to its own private state volume, so multiple ChatGPT-plan sessions can run
concurrently without one session's token refresh invalidating another's.

Pass `--provider openai` to use the plain `OPENAI_API_KEY` provider instead
— no ChatGPT plan, no proxy-managed auth. The default Codex network policy
allows `chatgpt.com` and `auth.openai.com` for the ChatGPT API and OAuth
exchange.

You can also switch an **existing** session's Codex auth in place with
`paude upgrade` — no need to recreate it. Use `--agent-provider` to remap
Codex's provider (or `--provider` if Codex is the session's primary agent):

```bash
# Swap Codex from ChatGPT-plan OAuth to the OpenAI API key
export OPENAI_API_KEY=your-api-key
paude upgrade my-project --agent-provider codex=openai
```

Export `OPENAI_API_KEY` **before** upgrading: it is read from your host
environment and injected into the session's proxy (the same key must be present
on later `start`/`connect`). The upgrade drops the `chatgpt` credential
provider, clears the old ChatGPT `auth.json`, and reconfigures Codex's
`config.toml` for the OpenAI API. To go back, run
`paude upgrade my-project --agent-provider codex=chatgpt` and `codex login`
inside the session again.

Paude merges the required HTTP/SSE provider settings into the session's
persistent Codex `config.toml` because the local MITM proxy does not support
the Responses WebSocket transport. Existing model, MCP, project trust, and
other user settings are preserved across starts and upgrades. Codex Apps are
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

### Persistent sessions and upgrades

Agent configuration, logins, conversation history, skills, and workspace data
are stored on each session's private volume. They survive `start`, `stop`, and
container recreation. Each session gets a writable `/pvc/.gitconfig`, seeded
from the host config when available or created empty otherwise. Your git
author identity is resolved on the host with `git config` (honoring global,
XDG, system, and `includeIf` config) and applied inside the container when the
seeded config lacks one, so commits are attributed correctly. All container
processes use it, so `git config --global` works consistently for local and
SSH-remote Docker/Podman sessions without modifying the host config.
After updating Paude, refresh a session with:

```bash
paude upgrade SESSION
```

The command performs a fresh build with the latest stable agent tooling and
migrates state from older containers before replacing them. Upgrades are
crash-safe: your `/pvc` data volume is reused in place and never removed, and if
an upgrade is interrupted (e.g. `Ctrl-C`) you can simply re-run
`paude upgrade SESSION` to finish it. `upgrade` can also add an agent to an
existing session in place, e.g. `paude upgrade SESSION --add-agent codex`. See
[Session Management](docs/SESSIONS.md) for the per-agent persistence paths.

### Backing up a session

To guard a long-running session against loss, snapshot it to a portable bundle:

```bash
paude stop my-project        # backup refuses to run on a live session
paude backup my-project      # writes ~/.config/paude/backups/<name>-<ts>.paude/
```

The bundle is a directory containing the session's `/pvc` data volume (workspace
+ agent state, as `pvc.tar.gz`) and a config manifest — everything needed to
recreate the session. Reconstructible pieces (proxy, network, secrets) are
excluded, and known agent credential files are always stripped, so a bundle
never stores a live token. Backup checks free disk space first (`--force` to
override) and shows live progress (bytes archived, throughput, elapsed) as it
runs. The archive is streamed straight to the bundle, so backing up a remote
session never needs scratch space on the remote host. Add `--remote-only` to
keep the bundle on the remote session's own host instead of downloading it —
handy on a slow connection to a fast remote session. `paude restore BUNDLE` is
planned; today it validates a bundle and prints the restore it would perform. See
[Session Management](docs/SESSIONS.md#backup--restore) for details.

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

# OpenCode
paude create --agent opencode --yolo --git my-project

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

### Port Forwarding

Some agents run a service inside the container that you want to reach from your host — for example a web dashboard. Forwarding is **opt-in and per-connection**: by default no container ports are exposed. Pass `--forward-port` when you attach to a session, on either `paude connect` or `paude start`:

```bash
# Forward container port 8372 to host port 8372
paude connect my-project --forward-port 8372

# Different host port than container port (HOST:CONTAINER)
paude connect my-project --forward-port 9000:8372

# Bind a non-loopback host interface (HOST_IP:HOST:CONTAINER)
paude connect my-project --forward-port 0.0.0.0:8372:8372

# Repeat the flag to forward multiple ports
paude connect my-project --forward-port 8372 --forward-port 5173

# Works the same on the start path (start a stopped session, then attach)
paude start my-project --forward-port 8372
```

`--forward-port` accepts three forms:

- `PORT` — same port on host and container (binds `127.0.0.1`)
- `HOST:CONTAINER` — map a different host port (binds `127.0.0.1`)
- `HOST_IP:HOST:CONTAINER` — bind a specific host interface (e.g. `0.0.0.0` to expose on your LAN)

Because forwarding is decided at attach time, you choose which ports to expose on every `connect`/`start`, and nothing is persisted with the session. Forwarding starts when you attach and stops when you disconnect. It works on both the Podman and Docker backends.

**macOS (podman machine):** Podman runs containers in a VM on macOS, but the forwarder's listener runs on your Mac host directly — it never binds inside the VM. Reaching the container uses the same `podman exec` path `paude connect` already relies on, so `--forward-port` behaves the same as on Linux; no extra `podman machine` port-publishing is needed.

**Loopback binding:** the forwarder reaches the service over `127.0.0.1` *inside* the container, so the in-container service must listen on `127.0.0.1` (or `0.0.0.0`) rather than a container-external interface. Every connection appears to the service as coming from `127.0.0.1`, which satisfies services that only accept loopback traffic.

#### Worked example: Gas City dashboard

The Gas City agent serves a dashboard inside the container on `127.0.0.1:8372`. To reach it from your host:

```bash
# Connect to a Gas City session and forward the dashboard port
paude connect my-project --forward-port 8372
```

While connected, paude prints `Port-forward active: http://127.0.0.1:8372`. Open <http://localhost:8372> in your host browser to view the dashboard.

**Remote hosts (SSH):** the forwarder runs on your local machine even when the container runs on a remote host, tunnelling each connection through `ssh … podman exec`. So the same flag works transparently over SSH — no manual tunnel required:

```bash
paude connect my-project --forward-port 8372
# Dashboard is reachable at http://localhost:8372 on your LOCAL machine
```

If you prefer to forward without paude, the equivalent manual SSH tunnel is:

```bash
ssh -L 8372:127.0.0.1:8372 user@remote
```

Note that a plain `ssh -L` reaches the remote *host's* loopback, so the container service must additionally be published to the remote host; paude's `--forward-port` avoids that by exec'ing directly into the container.

### Passing a Task

```bash
paude create --yolo my-project -a '-p "refactor the auth module"'
```

File copies also cross the SSH connection automatically. The local path is
always resolved on the machine where you run `paude`:

```bash
paude cp dgx-spark-codex:/pvc/workspace/output.log ./output.log
paude cp ./input.txt dgx-spark-codex:/pvc/workspace/input.txt
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
- [Orchestration](docs/ORCHESTRATION.md) — fire-and-forget workflow, harvest (including containers holding multiple repos), PRs
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
