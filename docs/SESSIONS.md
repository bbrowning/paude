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
| `upgrade` | Pulls current bases, rebuilds with the latest stable agent tooling, and recreates the session while preserving workspace and agent state; can also add agents (`--add-agent`, `--agents`) and reconfigure options (`--otel-endpoint`, `--allowed-domains`, `--allowed-endpoints`, `--gpu`/`--no-gpu`, `--yolo`/`--no-yolo`, `--provider`) |
| `remote` | Manages git remotes for code sync |
| `delete` | Removes all resources including volume |
| `backup` | Snapshots a stopped session (volume + config) to a portable bundle |
| `restore` | Rebuilds a session from a backup bundle (planned; currently a dry run) |
| `list` | Shows all sessions with version info |
| `status` | Shows enriched session status (activity, state, summary) |
| `harvest` | Pulls agent changes into a local branch, optionally creates a PR |
| `reset` | Resets session workspace and clears conversation history |
| `config` | Manages user defaults (`config show`, `config path`, `config init`) |
| `allowed-domains` | Views or modifies allowed egress domains for a session |
| `allowed-endpoints` | Views or modifies exact destination port exceptions |
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

# Add another agent to an existing session (preserves all data)
paude upgrade my-project --add-agent codex

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

When the new container starts, `upgrade` reconciles ownership of the reused
`/pvc` volume to the pinned runtime user, so a volume created before the runtime
UID was pinned stays readable and writable and agent logins survive the upgrade.
State migration is also best-effort: a source it cannot copy (for example a
read-only, host-mounted `~/.gitconfig` on a remote session) is skipped with a
warning instead of aborting the upgrade.

The legacy `--rebuild` option is still accepted for compatibility, but is no
longer necessary because upgrades always rebuild.

### Adding agents to an existing session

`upgrade` can add agents to a session that was created with a smaller set,
without recreating it or losing the workspace and existing agent state:

```bash
# Add a codex agent to a session that only had claude
paude upgrade my-project --add-agent codex

# Pick the new agent's inference provider (default for codex is chatgpt)
paude upgrade my-project --add-agent codex --agent-provider codex=openai

# Redefine the full agent set (first is primary); can reorder to change which
# agent launches by default
paude upgrade my-project --agents claude,codex
```

`--add-agent` appends agents and always keeps the current primary agent (the one
that launches by default). `--agents` redefines the whole set and makes the first
entry primary; in this release it must still include every installed agent
(removing agents is not yet supported). A newly added agent's provider defaults to
that agent's usual provider unless you override it with `--agent-provider`; the
provider is added to the session's credential set automatically. Provider logins
that happen inside the container (for example codex's ChatGPT OAuth) still need to
be completed on first use of the new agent.

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

## Backup & Restore

`paude backup` snapshots a session so you can recover it later — for example if
a long-running session's workspace is damaged, or you want a rollback point
before an autonomous run.

```bash
# Stop first — backup refuses to run on a live session
paude stop my-project
paude backup my-project

# Bundle is written to ~/.config/paude/backups/ by default
# Override the destination (a bundle directory path, or a directory to put it in)
paude backup my-project --output ~/backups/
paude backup my-project -o /tmp/my-project.paude
```

A backup is a `<name>-<timestamp>.paude/` directory (mode `0700`) with two files:

- `manifest.json` — the session's identity and configuration (agent/providers,
  allowed domains and endpoints, yolo, gpu, otel, workspace path,
  backend/engine, and SSH
  details for remote sessions).
- `pvc.tar.gz` — the session's `/pvc` data volume: the workspace plus all agent
  state, history, and skills.

A directory (rather than one wrapping tarball) means the multi-GB volume archive
is written to disk only once, which matters for large volumes. The archive is
produced by a throwaway container that tars the read-only volume to its stdout,
and that stream is piped straight into `pvc.tar.gz` (over SSH for remote
sessions) and hashed as it flows through — so nothing large is ever staged on
the engine host, which is what makes backing up a big *remote* session reliable
even when the remote host's `/tmp` or container storage is small. Live progress
(bytes archived, throughput, elapsed) is shown as it runs. Both files are mode
`0600`. Before starting, backup estimates the volume size and, if the
destination looks short on free space, asks you to re-run with `--force`.

For a remote session, add `--remote-only` to keep the bundle on the session's
own host instead of downloading it — useful when you're on a slow or
high-latency connection (e.g. hotel wifi) to a session that lives on a fast
one:

```bash
paude backup my-project --remote-only

# Choose where on the remote host the bundle lands
paude backup my-project --remote-only --output /srv/backups/
```

The volume is still tarred to stdout by the same throwaway container as a
local backup, but the remote shell redirects that stdout straight to a file on
its own disk instead of piping it back over SSH — so nothing large ever
crosses the link. The default destination mirrors the local convention
(`${XDG_CONFIG_HOME:-~/.config}/paude/backups/` on the remote host). Progress
is estimated by periodically polling the partial archive's size over a small
SSH call rather than counting bytes as they stream, since the client never
sees them. `--remote-only` requires a session created with `--host`.

Only the data volume is irreplaceable, so that is all a backup captures. The
proxy sidecar, network, CA/auth volumes, and credential secrets are rebuilt from
your host environment on the next `start`, exactly as they are for a fresh
`create`, so they are deliberately excluded.

**Backups never store live credentials.** paude keeps real provider credentials
on the proxy sidecar, not in the agent container, so `/pvc` normally holds no
secrets. As a safeguard, `paude backup` also always strips known agent auth files
(e.g. Gemini's `oauth_creds.json`, Cursor's `auth.json`). After a restore,
re-login inside the session for any agent that authenticates via an in-container
login flow.

Backup requires the session to be **stopped** so nothing is writing to the
workspace, git, or agent history databases while the snapshot is taken.

`paude restore` is planned. It will recreate the `/pvc` volume from a bundle and
rebuild the session around it (reusing the volume, then starting and registering
it, mirroring how `upgrade` recreates a container). Today it validates a bundle
and prints the restore it would perform:

```bash
paude restore ~/.config/paude/backups/my-project-20260810T101500Z.paude
```

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
