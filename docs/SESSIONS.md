# Session Management

Paude provides persistent sessions that survive container restarts.

```bash
# Quick start: create session for current directory (name derived from the directory)
paude create
paude start

# List all sessions (shorthand: just `paude`)
paude list
paude
```

## Commands

| Command | What It Does |
|---------|--------------|
| `create` | Creates session resources (container, volume) and starts them |
| `start` | Starts the container and connects |
| `stop` | Stops the container, preserves the volume |
| `connect` | Attaches to running session |
| `cp` | Copies files between local machine and session |
| `upgrade` | Pulls current bases, rebuilds with the latest stable agent tooling, and recreates the session while preserving workspace and agent state; can also reconfigure options (`--otel-endpoint`, `--allowed-domains`, `--gpu`/`--no-gpu`, `--yolo`/`--no-yolo`, `--provider`) |
| `remote` | Manages git remotes for code sync |
| `delete` | Removes all resources including volume |
| `list` | Shows all sessions with version info |
| `status` | Shows enriched session status (activity, state, summary) |
| `harvest` | Pulls agent changes into a local branch, optionally creates a PR |
| `reset` | Resets session workspace and clears conversation history |
| `config` | Manages user defaults (`config show`, `config path`, `config init`) |
| `allowed-domains` | Views or modifies allowed egress domains for a session |
| `blocked-domains` | Shows domains blocked by the proxy for a session |

## Examples

```bash
# Create session and push code in one step
paude create my-project --git

# Create a named session (starts container automatically)
paude create my-project

# Connect to the running session
paude connect my-project

# Work with the agent... then detach with Ctrl+b d

# Reconnect later
paude connect my-project

# Stop to save resources (preserves state)
paude stop my-project

# Restart - instant resume, no reinstall
paude start my-project

# Upgrade after updating paude
pip install --upgrade paude
paude list                         # Shows version and outdated indicator (*)
paude upgrade my-project           # Rebuilds image, preserves all data

# Delete session completely
paude delete my-project --confirm

# Remove a session's local config entry without contacting the backend
# (useful for orphaned or legacy sessions)
paude delete my-project --confirm --force
```

`upgrade` performs a fresh build even when the session already uses the
current Paude version. Before replacing the container, it migrates state from
older container writable layers into the session volume. This preserves agent
configuration, logins, conversation history, installed skills, and other
mutable state in addition to `/pvc/workspace`.

The legacy `--rebuild` option is still accepted for compatibility, but is no
longer necessary because upgrades always rebuild.

### Crash-safe, resumable upgrades

`upgrade` is safe to interrupt. Before tearing down the old container, it
records the session's fully-resolved configuration to a durable manifest on the
local host (`~/.config/paude/upgrades.json`, next to the session registry). The
session's `/pvc` data volume is reused in place and never removed during an
upgrade, so your workspace and agent state are never at risk.

If an upgrade is interrupted (e.g. `Ctrl-C`) or fails part-way, the manifest is
left in place and your data is intact. Simply re-run the same command to finish
it:

```bash
paude upgrade SESSION   # resumes and completes an interrupted upgrade
```

Resuming works even if the old container was already removed (the config is
read from the manifest instead of the container's labels) and for both local
and remote/SSH sessions (the manifest always lives on the local host). The
manifest is deleted automatically once the upgrade succeeds.

### Persisted agent state

| Agent | PVC-backed home paths |
|-------|-----------------------|
| Claude Code | `~/.claude`, `~/.claude.json` |
| Codex CLI | `~/.codex`, `~/.agents` |
| Cursor CLI | `~/.cursor`, `~/.config/cursor` |
| Gemini CLI | `~/.gemini`, `~/.agents` |
| OpenCode | `~/.config/opencode`, `~/.local/share/opencode`, `~/.local/state/opencode` |
| OpenClaw | `~/.openclaw` |
| Gas City | `~/.gc`, bundled-agent paths |

Shared writable Git and Dolt configuration is also stored on the session
volume. Regenerable caches and image-installed binaries are rebuilt instead
of persisted.

## Backend Selection

```bash
# Explicit backend selection
paude create my-project --backend=podman
paude create my-project --backend=docker
paude list --backend=podman
```

## Code Synchronization

Sessions use git for code synchronization. The easiest way is the `--git` flag on create:

```bash
# One-step: create session, push code+tags, set up origin
paude create my-project --git
paude connect my-project

# In container: gh pr list, git describe, etc. all work
```

The `--git` flag:
1. Creates the session and starts the container
2. If a local `origin` remote exists (the common case), clones directly from `origin` inside the container and pushes only your local-only commits as a delta. This uses the container's own network path to your git host, so it's faster than pushing everything through your local connection.
3. Falls back to a full push if there's no local `origin`, the branch is detached, the clone fails, or `--no-clone-origin` is passed. The fallback adds a `paude-<name>` git remote locally, pushes the current branch and all tags to the container, then sets `origin` inside the container from your local origin.
4. Either way, tags end up available inside the container (for `git describe`)

### Manual Code Sync

You can also set up git remotes manually:

```bash
# Create session (container starts automatically)
paude create my-project
paude connect my-project         # Connect in one terminal

# In another terminal: Set up remote and push code
paude remote add --push my-project  # Init git in container + push

# Later: Push more changes
git push paude-my-project main

# After the agent makes changes, pull them locally
git pull paude-my-project main

# List all paude git remotes
paude remote list

# Remove the remote for a specific session
paude remote remove my-project

# Remove remotes whose sessions no longer exist
paude remote cleanup
```

By default a session exposes one repo at `/pvc/workspace` as the `paude-<session>` remote. If a container holds more than one git repo, point additional remotes at their sub-paths with `--container-path` and give each a distinct `--remote` name:

```bash
paude remote add my-project --container-path /pvc/workspace/repos/api --remote rig-api
git -C ~/src/api fetch rig-api
```

Use a non-`paude-` remote name (as above) so `paude remote cleanup` doesn't remove it. See [Orchestration](ORCHESTRATION.md) for harvesting these sub-path repos.
