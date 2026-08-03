# Configuration

## Defaults & Precedence

Instead of typing long `paude create` commands every time, you can store defaults in configuration files. For example, this:

```bash
paude create --backend docker --yolo --git --allowed-domains default --allowed-domains golang
```

becomes simply:

```bash
paude create
```

### Precedence Order

Settings are resolved in layers (highest priority wins):

1. **CLI flags** — explicit flags on `paude create`
2. **Project config** — `paude.json` in the workspace
3. **User defaults** — `~/.config/paude/defaults.json`
4. **Built-in defaults** — hardcoded fallbacks

### User Defaults

User defaults apply to all your sessions across all projects. The file lives at `~/.config/paude/defaults.json` (or `$XDG_CONFIG_HOME/paude/defaults.json` if `XDG_CONFIG_HOME` is set).

Create a starter file with all fields:

```bash
paude config init
```

Then edit it to set the values you want. Any field set to `null` or omitted uses the built-in default.

**Full example:**

```json
{
  "defaults": {
    "backend": "docker",
    "agent": "claude",
    "provider": null,
    "agents": [],
    "providers": [],
    "agent-providers": {},
    "yolo": true,
    "git": true,
    "platform": "linux/amd64",
    "gpu": "all",
    "allowed-domains": ["default", "golang"],
    "otel-endpoint": null
  }
}
```

### Project Hints

Projects can declare defaults in their `paude.json` so that anyone cloning the repo gets the right settings automatically.

**In paude.json** — add a `"create"` section:

```json
{
  "base": "python:3.11-slim",
  "packages": ["make"],
  "create": {
    "allowed-domains": ["default", "golang"],
    "agent": "claude"
  }
}
```

Only `allowed-domains`, `agent`, `provider`, `agents`, `providers`,
`agent-providers`, `otel-endpoint`, and `forward-ports` are supported as
project-level create hints.

`agents` is the exact install set; the first entry is primary and launches.
`providers` is the credential set configured in the proxy and container, not a
positional mapping. `agent-providers` maps installed agents by name, and
unmapped agents use their defaults:

```json
{
  "agents": ["gascity", "claude", "codex"],
  "providers": ["vertex", "chatgpt"],
  "agent-providers": {
    "gascity": "vertex",
    "claude": "vertex",
    "codex": "chatgpt"
  }
}
```

When `providers` is omitted it is derived from the effective mappings. An
explicit list must cover every mapping and may include extra credentials.
Gas City does not implicitly install child CLIs.

### Domain Merging

Domains from user defaults and project config are **merged** (union). For example, if your user defaults specify `["default", "golang"]` and the project config specifies `["nodejs"]`, the resolved list is `["default", "golang", "nodejs"]`.

However, if you pass `--allowed-domains` on the CLI, it **overrides** entirely — no merging with user/project config occurs. The one exception is provider-required domains, which are always forced onto the allowlist regardless of what you pass — see [Network Domains](#network-domains).

### Forward-Port Resolution

Unlike domains, `forward-ports` don't merge across layers: the highest-precedence layer that sets any ports (CLI > project > user defaults) wins outright, replacing lower layers entirely.

### Inspecting Resolved Configuration

```bash
# Show resolved defaults with provenance (which layer each value came from)
paude config show

# Print the user config file path
paude config path

# Preview the full resolved configuration for a create command
paude create --dry-run
```

### Settings Reference

| Setting | User defaults | Project config | CLI flag | Built-in default |
|---------|:---:|:---:|:---:|---|
| `backend` | yes | — | `--backend` | `podman` |
| `agent` | yes | yes | `--agent` | `claude` |
| `yolo` | yes | — | `--yolo` | `false` |
| `git` | yes | — | `--git` | `false` |
| `platform` | yes | — | `--platform` | (none) |
| `allowed-domains` | yes | yes | `--allowed-domains` | `["default"]` |
| `gpu` | yes | — | `--gpu` / `--no-gpu` | (none) |
| `provider` | yes | yes | `--provider` | (none) |
| `agents` | yes | yes | `--agents` | `["claude"]` |
| `providers` | yes | yes | `--providers` | (none) |
| `agent-providers` | yes | yes | `--agent-provider` | agent defaults |
| `otel-endpoint` | yes | yes | `--otel-endpoint` | (none) |
| `forward-ports` | yes | yes | `--forward-port` | (none) |

> **Backend values**: `podman` (default) or `docker`.

## Network Domains

By default, paude runs a proxy sidecar that filters network access to Vertex AI, Python packages, GitHub, and agent-specific domains only.

```
┌─────────────────────────────────────────────────────────┐
│  paude-internal network (no direct internet)            │
│  ┌───────────┐        ┌───────────────────────────────┐ │
│  │  Agent    │───────▶│  Proxy (domain allowlist)     │─┼──▶ *.googleapis.com
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

# Add Go module proxy access
paude create --allowed-domains default --allowed-domains golang
```

The default allowlist includes:
- **vertexai**: Vertex AI and Google OAuth domains (`accounts.google.com`, `oauth2.googleapis.com`, `*-aiplatform.googleapis.com`, etc.)
- **python**: Python package repositories (`.pypi.org`, `.pythonhosted.org`, `.pytorch.org`)
- **github**: GitHub domains (`github.com`, `api.github.com`, `raw.githubusercontent.com`, etc.)

Agent-specific defaults are added automatically:
- **Claude Code**: `.claude.ai`, `.anthropic.com`
- **Codex CLI** (default `chatgpt` provider; not added for `--provider openai`): `chatgpt.com`, `.chatgpt.com`, `auth.openai.com`
- **Cursor CLI**: `.cursor.com`, `.cursor.sh`, `.cursor-cdn.com`, `.cursorapi.com` (HTTP/1.1 mode is automatically enabled for proxy compatibility)
- **Gemini CLI**: `cloudcode-pa.googleapis.com`, `play.googleapis.com`, plus the `nodejs` alias
- **OpenCode**: `opencode.ai`, `.opencode.ai` (plus `chatgpt.com`, `.chatgpt.com`, `auth.openai.com` when using the `chatgpt` provider)
- **Gas City** (orchestration agent): contributes no child-agent domains itself. List each child CLI explicitly so its domains are included.
- **OpenClaw**: `.anthropic.com`, `.openai.com`, `.duckduckgo.com`, `wttr.in`, `api.open-meteo.com`

Credential providers (`--providers`) contribute domains independently of the
agent mappings. When the credential set is derived, the effective mapped
providers contribute the same domains:
- `vertex` / `google`: the `vertexai` domains
- `openai`: `.openai.com`
- `anthropic`: the `claude` domains
- `cursor`: the `cursor` domains
- `chatgpt`: the `chatgpt` domains. An agent mapped to ChatGPT also marks these
  domains as required, so they remain available with an explicit allowlist.

Opt-in language ecosystem aliases:
- **golang**: Go modules (`go.dev`, `proxy.golang.org`, `sum.golang.org`, `dl.google.com`, `storage.googleapis.com`)
- **nodejs**: npm/Yarn registries (`.nodejs.org`, `.npmjs.org`, `.yarnpkg.com`)
- **rust**: Cargo/rustup (`crates.io`, `static.crates.io`, `static.rust-lang.org`)

Opt-in OpenClaw plugin aliases, for skill packages that talk to external services:
- **clawhub**: OpenClaw skill registry (`clawhub.ai`, `.clawhub.ai`, `registry.npmjs.org`)
- **whatsapp**: WhatsApp Web (`web.whatsapp.com`, `.whatsapp.net`)
- **telegram**: Telegram Bot API (`api.telegram.org`)
- **discord**: Discord bot/gateway API (`.discord.com`, `gateway.discord.gg`, `.discordapp.com`)
- **slack**: Slack API (`.slack.com`)

> **Note**: `pypi` is a backward-compatible alias for `python`, and `codex` is a backward-compatible alias for `chatgpt`.

**Special values**: `all` (unrestricted), `default` (vertexai + python + github + agent-specific). Any other alias name listed above, or a raw domain, can also be passed directly. Specifying domains without `default` replaces the allowlist entirely.

## Diagnosing Blocked Domains

When a tool or package install fails due to network filtering, check what the proxy blocked:

```bash
# 1. View blocked domains
paude blocked-domains my-session

# Output:
#   Blocked domains for session 'my-session':
#
#     registry.npmjs.org     8 requests
#     cdn.jsdelivr.net       3 requests
#
#   2 unique domain(s) blocked (11 total requests).
#
#   Tip: To allow a domain, run:
#     paude allowed-domains my-session --add <domain>

# 2. Allow the domain you need
paude allowed-domains my-session --add registry.npmjs.org

# 3. Verify it was added
paude allowed-domains my-session

# 4. Retry the failed operation inside the session
```

Use `--raw` to see the full proxy log with timestamps:

```bash
paude blocked-domains my-session --raw
```

`allowed-domains` also supports removing or wholesale-replacing a session's allowlist. `--add`, `--remove`, and `--replace` are mutually exclusive; running `allowed-domains` with no flag lists the current domains:

```bash
# Remove a domain
paude allowed-domains my-session --remove registry.npmjs.org

# Replace the entire allowlist
paude allowed-domains my-session --replace default,pypi
```

## GitHub CLI Access

Paude installs the `gh` CLI in the container and includes GitHub domains in the default network allowlist. To use `gh` for read-only operations (e.g., fetching issues, PRs, or code), set a fine-grained personal access token before creating or starting the session:

```bash
# Set once in your shell profile, or export before running paude:
export PAUDE_GITHUB_TOKEN=ghp_yourtoken

paude create my-project
paude start my-project
# Inside the container, gh is authenticated automatically
```

The token is never handed to the agent's own container:
- `PAUDE_GITHUB_TOKEN` is read on the host and stored only on the network-filtering proxy sidecar. See [Remote Hosts & Docker Backend](REMOTE.md) for how storage differs between the Podman and Docker backends
- The agent container's own `GH_TOKEN` environment variable is always a non-functional placeholder; the proxy transparently attaches the real token to requests it forwards to GitHub's API
- `GH_CONFIG_DIR=/tmp/gh-config` ensures no cached host credentials are ever consulted

**Security notes**:
- The host's `GH_TOKEN` environment variable is **never** auto-propagated to the container
- Use a **fine-grained PAT** scoped to read-only permissions on specific repositories
- Do not use tokens with write access; they could allow the agent to push code to GitHub
- The token is never written to host disk as a paude-managed file

Create a fine-grained read-only PAT at:
https://github.com/settings/tokens?type=beta

Select only the repositories the agent should access, and grant only **Contents: Read-only** (plus **Metadata: Read-only** which is always required).

## Workflow Modes

**Execution mode** (default): `paude create`
- Network filtered via proxy
- The agent prompts for confirmation before edits and commands

**Autonomous mode**: `paude create --yolo`
- Same network filtering
- The agent edits files and runs commands without confirmation prompts
- Passes the agent's skip-permissions flag (e.g., `--dangerously-skip-permissions` for Claude Code)

**Research mode**: `paude create --allowed-domains all`
- Full network access for web searches, documentation
- Treat outputs more carefully (prompt injection via web content is possible)

## Custom Container Environments (BYOC)

Paude supports custom container configurations via `paude.json`.

**Using paude.json**:

```json
{
    "base": "python:3.11-slim",
    "packages": ["make", "gcc"],
    "setup": "pip install -r requirements.txt"
}
```

**paude.json properties:**

| Property | Description |
|----------|-------------|
| `base` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `build.args` | Build arguments for Dockerfile |
| `packages` | Additional system packages to install |
| `setup` | Run after first start |

## GPU Passthrough

Pass GPU devices to the container for GPU-accelerated workloads. This works with both local and [remote host](REMOTE.md) sessions.

```bash
# All GPUs
paude create my-project --gpu all

# Specific devices
paude create my-project --gpu=device=0,1

# Explicitly disable (overrides user defaults)
paude create my-project --no-gpu
```

Set GPU passthrough as a default in `~/.config/paude/defaults.json`:

```json
{
  "defaults": {
    "gpu": "all"
  }
}
```

Use `--no-gpu` on the CLI to override the default for a specific session.

## Verifying Configuration

```bash
# Verify configuration without building or running
paude create --dry-run

# Force rebuild after changing config
paude create --rebuild
```
