# Configuration

## Defaults & Precedence

Instead of typing long `paude create` commands every time, you can store defaults in configuration files. For example, this:

```bash
paude create --backend openshift --yolo --git --allowed-domains default --allowed-domains golang
```

becomes simply:

```bash
paude create
```

### Precedence Order

Settings are resolved in layers (highest priority wins):

1. **CLI flags** — explicit flags on `paude create`
2. **Project config** — `paude.json` or `devcontainer.json` in the workspace
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
    "backend": "openshift",
    "agent": "claude",
    "yolo": true,
    "git": true,
    "pvc-size": "10Gi",
    "platform": "linux/amd64",
    "gpu": "all",
    "allowed-domains": ["default", "golang"],
    "openshift": {
      "context": "my-cluster",
      "namespace": "my-ns",
      "resources": {
        "requests": {
          "cpu": "250m",
          "memory": "2Gi"
        },
        "limits": {
          "cpu": "2",
          "memory": "4Gi"
        }
      },
      "build-resources": {
        "requests": {
          "cpu": "500m",
          "memory": "1Gi"
        },
        "limits": {
          "cpu": "1",
          "memory": "2Gi"
        }
      }
    }
  }
}
```

### Project Hints

Projects can declare defaults in their `paude.json` or `devcontainer.json` so that anyone cloning the repo gets the right settings automatically.

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

**In devcontainer.json** — nest under `customizations.paude.create`:

```json
{
  "image": "python:3.11-slim",
  "customizations": {
    "paude": {
      "create": {
        "allowed-domains": ["default", "nodejs"],
        "agent": "gemini"
      }
    }
  }
}
```

Only `allowed-domains`, `agent`, and `provider` are supported as project-level create hints.

### Domain Merging

Domains from user defaults and project config are **merged** (union). For example, if your user defaults specify `["default", "golang"]` and the project config specifies `["nodejs"]`, the resolved list is `["default", "golang", "nodejs"]`.

However, if you pass `--allowed-domains` on the CLI, it **overrides** entirely — no merging occurs.

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
| `pvc-size` | yes | — | `--pvc-size` | `10Gi` |
| `platform` | yes | — | `--platform` | (none) |
| `allowed-domains` | yes | yes | `--allowed-domains` | `["default"]` |
| `gpu` | yes | — | `--gpu` / `--no-gpu` | (none) |
| `openshift.context` | yes | — | `--openshift-context` | (none) |
| `openshift.namespace` | yes | — | `--openshift-namespace` | (none) |
| `openshift.resources` | yes | — | — | (none) |
| `openshift.build-resources` | yes | — | — | (none) |
| `provider` | yes | yes | `--provider` | (none) |
| `storage-class` | — | — | `--storage-class` | (none) |

> **Backend values**: `podman` (default), `docker`, or `openshift`.

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
- **Cursor CLI**: `.cursor.com`, `.cursor.sh`, `.cursor-cdn.com`, `.cursorapi.com` (HTTP/1.1 mode is automatically enabled for proxy compatibility)
- **Gemini CLI**: `cloudcode-pa.googleapis.com`, `play.googleapis.com`, plus the `nodejs` alias

Opt-in language ecosystem aliases:
- **golang**: Go modules (`go.dev`, `proxy.golang.org`, `sum.golang.org`, `dl.google.com`, `storage.googleapis.com`)
- **nodejs**: npm/Yarn registries (`.nodejs.org`, `.npmjs.org`, `.yarnpkg.com`)
- **rust**: Cargo/rustup (`crates.io`, `static.crates.io`, `static.rust-lang.org`)

> **Note**: `pypi` is a backward-compatible alias for `python`.

**Special values**: `all` (unrestricted), `default` (vertexai + python + github + agent-specific), `vertexai`, `python`, `golang`, `nodejs`, `rust`, `github`. Specifying domains without `default` replaces the allowlist entirely.

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

## GitHub CLI Access

Paude installs the `gh` CLI in the container and includes GitHub domains in the default network allowlist. To use `gh` for read-only operations (e.g., fetching issues, PRs, or code), set a fine-grained personal access token before connecting:

```bash
# Set once in your shell profile, or export before running paude:
export PAUDE_GITHUB_TOKEN=ghp_yourtoken

paude start my-project
# Inside the container, gh is authenticated automatically
```

Or pass it explicitly for a single session:

```bash
paude start --github-token ghp_yourtoken my-project
paude connect --github-token ghp_yourtoken my-project
```

The token is injected at connect time only:
- **Podman**: passed as `-e GH_TOKEN=...` to `podman exec` (not stored in the container definition)
- **OpenShift**: written to `/credentials/github_token` in the pod's tmpfs
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

See [`examples/README.md`](../examples/README.md) for more configurations (Python, Node.js, Go).

**paude.json properties:**

| Property | Description |
|----------|-------------|
| `base` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `build.args` | Build arguments for Dockerfile |
| `packages` | Additional system packages to install |
| `setup` | Run after first start |

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
