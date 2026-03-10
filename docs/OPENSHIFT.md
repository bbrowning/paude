# OpenShift Backend

Run AI coding agents in OpenShift/Kubernetes pods with persistent sessions, credential management, and network filtering.

## Prerequisites

1. **oc CLI** - OpenShift command-line tools installed and in PATH
2. **Cluster Access** - Logged in to an OpenShift cluster (`oc login`)
3. **gcloud credentials** - Vertex AI authentication at `~/.config/gcloud`

## Quick Start

```bash
# Verify cluster connectivity
oc whoami
oc project

# Create a session and push code in one step
paude create --backend=openshift --git

# Or with explicit namespace
paude create --backend=openshift --openshift-namespace=my-namespace

# Connect to the running session
paude connect
```

## How It Works

1. **Binary Build**: Container image is built on-cluster via OpenShift BuildConfig and `oc start-build`
2. **Pod Creation**: A pod is created with persistent storage and credentials injected
3. **Session Persistence**: tmux inside the pod preserves your agent session across reconnects
4. **Git-Based Sync**: Use `--git` on create or `paude remote add` and `git push/pull` to sync code
5. **Network Filtering**: NetworkPolicy restricts pod egress to approved destinations

## Session Management

Paude uses a unified session model across all backends. Sessions are persistent by default, surviving pod restarts via StatefulSets and PersistentVolumeClaims.

### Persistent Sessions

```bash
# Create session and push code in one step
paude create my-project --backend=openshift --git

# Connect and work with the agent... then detach with Ctrl+b d
paude connect my-project

# Pull changes made by the agent
git pull paude-my-project main

# Stop to save cluster resources (scales to 0, preserves PVC)
paude stop my-project --backend=openshift

# Restart - instant resume, everything still there
paude start my-project --backend=openshift

# List all sessions
paude list --backend=openshift

# Delete session completely (removes StatefulSet + PVC)
paude delete my-project --confirm --backend=openshift
```

### Session Lifecycle

| State | StatefulSet Replicas | Pod | PVC | Files |
|-------|---------------------|-----|-----|-------|
| Created | 0 | None | Created | Empty |
| Started | 1 | Running | Bound | Push via git |
| Stopped | 0 | None | Retained | Preserved |
| Deleted | Deleted | Deleted | Deleted | Gone |

### OpenShift-Specific Options

```bash
# Custom PVC size
paude create my-project --backend=openshift --pvc-size=50Gi

# Custom storage class
paude create my-project --backend=openshift --storage-class=fast-ssd
```

## Configuration

### CLI Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--backend=openshift` | Use OpenShift backend | `podman` |
| `--openshift-namespace=NAME` | Kubernetes namespace | current context namespace |
| `--openshift-context=NAME` | kubeconfig context | current |
| `--allowed-domains all` | Disable network filtering | `default` (vertexai + pypi + github) |
| `--yolo` | Skip agent permission prompts | `False` |

**Notes:**
- The namespace must already exist - paude will not create namespaces
- If no namespace is specified, paude uses the current namespace from your kubeconfig context

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PAUDE_REGISTRY` | Container registry for pulling and building base images | `quay.io/bbrowning` |

## Security

### Credential Security (tmpfs)

Credentials are stored in RAM-only storage for enhanced security:

**Security Model:**
- Credentials use a tmpfs (Memory-backed) emptyDir volume at `/credentials`
- Credentials never persist to disk - stored only in RAM
- Automatically cleared when pod stops or restarts
- Cannot be recovered from PVC snapshots or disk images
- Refreshed on every `paude connect` for fresh tokens

**Configuration Sync:**

Configuration is synced via `oc cp` to tmpfs on session start and reconnect:

**Synced from host:**
- `~/.config/gcloud` → gcloud credentials for Vertex AI authentication
- `~/.gitconfig` → Git identity configuration
- `~/.claude/` → Agent config directory (for Claude Code), including:
  - `settings.json`, `credentials.json` - Core settings
  - `plugins/` - Installed plugins and marketplace metadata
  - `CLAUDE.md` - Global instructions
- `~/.claude.json` → Agent preferences

**Excluded (session-specific):**
- `history.jsonl`, `tasks/`, `todos/` - Session state
- `cache/`, `stats-cache.json` - Caches
- `debug/`, `file-history/` - Debug logs

Plugin paths are automatically rewritten from host paths to container paths.

**Credential Refresh:**
- **First connect** (after pod start): Full sync of gcloud, claude config, and gitconfig
- **Reconnect** (subsequent connects): Only gcloud credentials refreshed (fast)
- This ensures fresh OAuth tokens propagate if you re-authenticate locally
- Long-running pods stay current with local credential changes

### Network Filtering

By default, sessions run with restricted network access:

- **Allowed**: DNS resolution, Vertex AI APIs (*.googleapis.com), PyPI (*.pypi.org), GitHub (github.com, api.github.com), plus agent-specific domains
- **Blocked**: All other external traffic

NetworkPolicy enforces egress restrictions at the Kubernetes level. Use `--allowed-domains all` to disable filtering for unrestricted access.

### Pod Security

Pods run with:
- Non-root user
- Dropped capabilities
- Read-only credential mounts

## Troubleshooting

### "oc: command not found"

Install the OpenShift CLI:
```bash
# macOS
brew install openshift-cli

# Linux (download from Red Hat)
# https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/
```

### "not logged in" errors

Login to your cluster:
```bash
oc login https://api.your-cluster.example.com:6443
```

### "namespace doesn't exist"

Paude requires the namespace to already exist - it will not create namespaces. Either:

1. Switch to an existing namespace:
```bash
oc project my-existing-namespace
```

2. Or specify an existing namespace explicitly:
```bash
paude create --backend=openshift --openshift-namespace=my-namespace
```

3. Or ask an administrator to create the namespace for you.

### Image build failures

Paude builds container images on-cluster using OpenShift Binary Build (BuildConfig + `oc start-build`). If the build fails:

```bash
# Check build logs
oc logs -f bc/paude-build -n <namespace>

# List builds and their status
oc get builds -n <namespace>

# Describe a failed build for events and errors
oc describe build paude-build-1 -n <namespace>
```

Common causes:
- Insufficient cluster resources for the build pod
- BuildConfig not created (check `oc get bc -n <namespace>`)
- Image stream issues (check `oc get is -n <namespace>`)

### Pod stuck in Pending

Check pod events:
```bash
oc describe pod paude-session-<ID> -n paude
```

Common causes:
- Insufficient cluster resources
- Image pull failures
- PVC provisioning issues

### Code sync issues

Paude uses git for code synchronization. Set up the remote first:
```bash
paude remote add SESSION_ID
```

Then use standard git commands:
```bash
git push paude-SESSION_ID main     # Push code to session
git pull paude-SESSION_ID main     # Pull changes from session
```

For merge conflicts, use normal git workflows (rebase, merge, etc.).

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    OpenShift Cluster                     │
│  ┌────────────────────────────────────────────────────┐ │
│  │                   paude namespace                   │ │
│  │  ┌──────────────────┐    ┌───────────────────────┐ │ │
│  │  │ paude-session-X  │    │    NetworkPolicy      │ │ │
│  │  │  ┌────────────┐  │    │  (egress filtering)   │ │ │
│  │  │  │   paude    │  │    └───────────────────────┘ │ │
│  │  │  │ container  │  │                              │ │
│  │  │  │  + tmux    │  │    ┌───────────────────────┐ │ │
│  │  │  └────────────┘  │    │  tmpfs: /credentials  │ │ │
│  │  │                  │    │  (RAM-only, ephemeral) │ │ │
│  │  │  Mounts:         │    │  - gcloud creds       │ │ │
│  │  │  - /pvc (PVC)    │    │  - ~/.claude/ dir     │ │ │
│  │  │  - /credentials  │    │  - gitconfig          │ │ │
│  │  │    (tmpfs)       │    └───────────────────────┘ │ │
│  │  └──────────────────┘                              │ │
│  │         ↑                                          │ │
│  │         │ git push/pull (code) / oc cp (creds)     │ │
│  │         ↓                                          │ │
│  └─────────┼──────────────────────────────────────────┘ │
└────────────┼────────────────────────────────────────────┘
             │
    ┌────────┴────────┐
    │  Local Machine  │
    │  - workspace    │
    │  - ~/.claude/   │
    │  - credentials  │
    │  - paude CLI    │
    └─────────────────┘
```

## Comparison with Podman Backend

| Feature | Podman | OpenShift |
|---------|--------|-----------|
| Session Persistence | Yes (named volumes) | Yes (tmux + PVC) |
| Network Disconnect | Session lost | Session preserved |
| Code Sync | git push/pull | git push/pull |
| Config Sync | Mounted at start | oc cp at connect |
| Multi-machine | No | Yes |
| Resource Isolation | Container | Pod + namespace |
| Setup Complexity | Low | Medium |

## Limitations

- **No SSH mounts**: Git push via SSH is not available (same as Podman backend)
- **Git workflow required**: Must use git to sync code (no automatic file sync)
- **Cluster dependency**: Requires active OpenShift cluster access
