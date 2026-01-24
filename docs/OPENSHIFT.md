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

# Start a session
paude --backend=openshift

# Or with explicit namespace
paude --backend=openshift --openshift-namespace=my-namespace
```

## How It Works

1. **Image Push**: Local paude container image is pushed to the OpenShift internal registry
2. **Pod Creation**: A pod is created with your workspace mounted and credentials injected
3. **Session Persistence**: tmux inside the pod preserves your Claude session across reconnects
4. **File Sync**: `oc rsync` synchronizes files between local and remote workspace
5. **Network Filtering**: NetworkPolicy restricts pod egress to approved destinations

## Session Management

Paude now uses a unified session model across all backends. Sessions are persistent by default, surviving pod restarts via StatefulSets and PersistentVolumeClaims.

### Quick Start (Ephemeral)

```bash
# Start immediately (creates ephemeral session)
paude --backend=openshift
```

### Persistent Sessions

```bash
# Create a named session (without starting)
paude create my-project --backend=openshift

# Start the session (scales StatefulSet, syncs files, connects)
paude start my-project --backend=openshift

# Work in Claude... then detach with Ctrl+b d

# Reconnect later
paude connect my-project --backend=openshift

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
| Started | 1 | Running | Bound | Synced from local |
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

### Configuration Sync

Configuration is synced via `oc rsync` to PVC storage on session start:

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
paude --backend=openshift --openshift-namespace=my-namespace
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

# Run paude with your registry
paude --backend=openshift --openshift-registry=quay.io/myuser
```

**Port-forward fallback (experimental):**

If no external route or registry is specified, paude attempts to use `oc port-forward`. This is unstable for large images and may fail with "connection refused" errors.

If you see TLS certificate errors with port-forward:
```bash
paude --backend=openshift --no-openshift-tls-verify
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

### File sync issues

Manual sync:
```bash
paude sync SESSION_ID --backend=openshift
```

Or use oc rsync directly:
```bash
oc rsync ./local-dir/ pod-name:/workspace/ -n paude
```

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
│  │  │  └────────────┘  │    │   PVC: paude-config   │ │ │
│  │  │                  │    │  - gcloud creds       │ │ │
│  │  │  Mounts:         │    │  - ~/.claude/ dir     │ │ │
│  │  │  - /workspace    │    │    (incl. plugins)    │ │ │
│  │  │  - /pvc/config   │    │  - gitconfig          │ │ │
│  │  └──────────────────┘    └───────────────────────┘ │ │
│  │         ↑                                          │ │
│  │         │ oc rsync (workspace + config)            │ │
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
| Session Persistence | No (ephemeral) | Yes (tmux) |
| Network Disconnect | Session lost | Session preserved |
| File Sync | Direct mount | oc rsync |
| Plugin Sync | Direct mount | oc rsync (auto path rewrite) |
| Multi-machine | No | Yes |
| Resource Isolation | Container | Pod + namespace |
| Setup Complexity | Low | Medium |

## Limitations

- **No SSH mounts**: Git push via SSH is not available (same as Podman backend)
- **No GitHub CLI**: `gh` operations are not available (same as Podman backend)
- **Initial sync delay**: Large workspaces take time to sync initially
- **Cluster dependency**: Requires active OpenShift cluster access
