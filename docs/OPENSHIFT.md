# OpenShift Backend

Run Claude Code in OpenShift/Kubernetes pods with persistent sessions, credential management, and network filtering.

## Prerequisites

1. **oc CLI** - OpenShift command-line tools installed and in PATH
2. **Cluster Access** - Logged in to an OpenShift cluster (`oc login`)
3. **Podman** - For building and pushing images locally
4. **gcloud credentials** - Vertex AI authentication at `~/.config/gcloud`

## Quick Start

```bash
# Verify cluster connectivity
oc whoami
oc project

# Create a session
paude create --backend=openshift

# Or with explicit namespace
paude create --backend=openshift --openshift-namespace=my-namespace

# Start the session
paude start
```

## How It Works

1. **Image Push**: Local paude container image is pushed to the OpenShift internal registry
2. **Pod Creation**: A pod is created with persistent storage and credentials injected
3. **Session Persistence**: tmux inside the pod preserves your Claude session across reconnects
4. **Git-Based Sync**: Use `paude remote add` and `git push/pull` to sync code
5. **Network Filtering**: NetworkPolicy restricts pod egress to approved destinations

## Session Management

Paude uses a unified session model across all backends. Sessions are persistent by default, surviving pod restarts via StatefulSets and PersistentVolumeClaims.

### Persistent Sessions

```bash
# Create a named session (without starting)
paude create my-project --backend=openshift

# Start the session (scales StatefulSet, connects)
paude start my-project --backend=openshift

# Set up git remote for code sync
paude remote add my-project

# Push code to the session
git push paude-my-project main

# Connect and work with Claude... then detach with Ctrl+b d
paude connect my-project --backend=openshift

# Pull changes made by Claude
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
| `--openshift-registry=URL` | Container registry URL | auto-detect |
| `--no-openshift-tls-verify` | Disable TLS certificate verification when pushing | N/A |
| `--allow-network` | Disable network filtering | `False` |
| `--yolo` | Skip Claude permission prompts | `False` |

**Notes:**
- The namespace must already exist - paude will not create namespaces
- If no namespace is specified, paude uses the current namespace from your kubeconfig context
- If the OpenShift internal registry route is not exposed, paude will attempt `oc port-forward` (unstable for large images)
- **Recommended**: Use `--openshift-registry` to specify an external registry (e.g., `quay.io/myuser`) for reliable image pushing

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PAUDE_REGISTRY` | Custom container registry | `quay.io/bbrowning` |

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
- `~/.claude/` → Full Claude config directory, including:
  - `settings.json`, `credentials.json` - Core settings
  - `plugins/` - Installed plugins and marketplace metadata
  - `CLAUDE.md` - Global instructions
- `~/.claude.json` → Claude preferences

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

- **Allowed**: DNS resolution, Vertex AI APIs (*.googleapis.com)
- **Blocked**: All other external traffic

NetworkPolicy enforces egress restrictions at the Kubernetes level. Use `--allow-network` to disable filtering for unrestricted access.

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

### Image push failures

Paude tries these methods in order to push images:

1. **External registry** - If `--openshift-registry` is specified
2. **Registry route** - If the internal registry has an exposed route
3. **Port-forward** - Automatic fallback using `oc port-forward`

Check if the registry route exists:
```bash
oc get route -n openshift-image-registry
```

**Using an external registry (recommended):**

The most reliable approach is to use an external registry like Quay.io or Docker Hub:

```bash
# Login to your registry first
podman login quay.io

# Create session with your registry
paude create --backend=openshift --openshift-registry=quay.io/myuser
```

**Port-forward fallback (experimental):**

If no external route or registry is specified, paude attempts to use `oc port-forward`. This is unstable for large images and may fail with "connection refused" errors.

If you see TLS certificate errors with port-forward:
```bash
paude create --backend=openshift --no-openshift-tls-verify
```

If port-forward fails with connection errors, use an external registry instead.

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
- **No GitHub CLI**: `gh` operations are not available (same as Podman backend)
- **Git workflow required**: Must use git to sync code (no automatic file sync)
- **Cluster dependency**: Requires active OpenShift cluster access
