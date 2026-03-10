# Configuration

## Network Domains

By default, paude runs a proxy sidecar that filters network access to Vertex AI, Python packages, and GitHub only.

```
┌─────────────────────────────────────────────────────────┐
│  paude-internal network (no direct internet)            │
│  ┌───────────┐        ┌───────────────────────────────┐ │
│  │  Agent    │───────▶│  Proxy (squid allowlist)      │─┼──▶ *.googleapis.com
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
- **vertexai**: Vertex AI and Google OAuth domains (`.googleapis.com`, `.google.com`)
- **python**: Python package repositories (`.pypi.org`, `.pythonhosted.org`, `download.pytorch.org`)

Agent-specific defaults are added automatically:
- **Claude Code**: `.claude.ai`, `.anthropic.com`
- **Gemini CLI**: `cloudcode-pa.googleapis.com`, `play.googleapis.com`, plus the `nodejs` alias

Opt-in language ecosystem aliases:
- **golang**: Go modules (`go.dev`, `proxy.golang.org`, `sum.golang.org`, `dl.google.com`, `storage.googleapis.com`)
- **nodejs**: npm/Yarn registries (`registry.npmjs.org`, `.npmjs.org`, `.yarnpkg.com`)
- **rust**: Cargo/rustup (`crates.io`, `static.crates.io`, `static.rust-lang.org`)

> **Note**: `pypi` is a backward-compatible alias for `python`.

**Special values**: `all` (unrestricted), `default` (vertexai + python + github), `vertexai`, `python`, `golang`, `nodejs`, `rust`, `github`. Specifying domains without `default` replaces the allowlist entirely.

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
- **OpenShift**: written to `/credentials/github_token` in the pod's tmpfs, wiped by the credential watchdog on inactivity
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

## Verifying Configuration

```bash
# Verify configuration without building or running
paude create --dry-run

# Force rebuild after changing config
paude create --rebuild
```
