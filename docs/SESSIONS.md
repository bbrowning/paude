# Session Management

Paude provides persistent sessions that survive container/pod restarts.

```bash
# Quick start: create session for current directory (uses directory name)
paude create
paude start

# List all sessions (shorthand: just `paude`)
paude list
paude
```

## Commands

| Command | What It Does |
|---------|--------------|
| `create` | Creates session resources (container/StatefulSet, volume/PVC) and starts them |
| `start` | Starts container/pod and connects |
| `stop` | Stops container/pod, preserves volume |
| `connect` | Attaches to running session |
| `cp` | Copies files between local machine and session |
| `upgrade` | Upgrades session to current paude version (preserves data) |
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
```

## Backend Selection

```bash
# Explicit backend selection
paude create my-project --backend=podman
paude create my-project --backend=docker
paude create my-project --backend=openshift
paude list --backend=podman

# Backend-specific options
paude create my-project --backend=openshift \
  --pvc-size=50Gi \
  --storage-class=fast-ssd
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
2. Adds a `paude-<name>` git remote locally
3. Pushes the current branch and all tags to the container
4. Sets the `origin` remote inside the container (from your local origin)
5. Tags are available inside the container (for `git describe`)

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
```
