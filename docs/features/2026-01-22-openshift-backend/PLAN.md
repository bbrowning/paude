# OpenShift Backend: Implementation Plan

## Goal

Enable paude to run Claude Code on OpenShift/Kubernetes clusters while maintaining:
- Local code editing experience
- Security model (network filtering, credential protection)
- Session persistence across network interruptions
- Seamless user experience ("feels like local")

## Design Principles

### 1. Backend Abstraction
All container backends (Podman, OpenShift) implement a common interface. The CLI delegates to the appropriate backend based on configuration.

### 2. Security Parity
OpenShift backend provides the same security guarantees as local Podman:
- Network isolation via separate pods + NetworkPolicy (equivalent to Podman internal network)
- DNS-based egress filtering via squid proxy pod
- Read-only credential mounts
- No SSH keys or GitHub CLI access

### 3. Progressive Enhancement
Each phase delivers working functionality. Later phases add polish and advanced features.

### 4. Minimal New Dependencies
- DevSpace: Required for file sync (user installs), or use built-in oc rsync
- tmux: Bundled in container image
- oc CLI: User already has (for OpenShift access)

## Architecture Overview

### Backend Interface

```python
from typing import Protocol
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Session:
    id: str
    status: str  # "running", "stopped", "error"
    workspace: Path
    created_at: str

class Backend(Protocol):
    """Container backend interface."""

    def start_session(
        self,
        image: str,
        workspace: Path,
        env: dict[str, str],
        network_restricted: bool = True,
    ) -> Session:
        """Start a new Claude session."""
        ...

    def attach_session(self, session_id: str) -> int:
        """Attach to running session, return exit code."""
        ...

    def stop_session(self, session_id: str) -> None:
        """Stop and cleanup session."""
        ...

    def list_sessions(self) -> list[Session]:
        """List all sessions for current user."""
        ...

    def sync_workspace(
        self,
        session_id: str,
        direction: str = "both"
    ) -> None:
        """Sync files between local and remote workspace."""
        ...
```

### Module Structure

```
src/paude/
├── backends/
│   ├── __init__.py
│   ├── base.py          # Backend Protocol definition
│   ├── podman.py        # Refactored from current code
│   └── openshift.py     # New OpenShift backend
├── sync/
│   ├── __init__.py
│   ├── devspace.py      # DevSpace sync wrapper
│   └── oc_rsync.py      # oc rsync fallback
├── container/           # Existing (becomes Podman-specific)
│   ├── podman.py
│   ├── image.py
│   ├── network.py
│   └── runner.py
└── cli.py               # Updated with --backend flag
```

### Session Lifecycle

```
User runs: paude --backend=openshift

  1. Check for existing session
     │
     ├─ Session exists ──► Attach via oc exec + tmux attach
     │
     └─ No session ──► Create new session:
                        │
                        ├─ Push images to OpenShift registry (paude + squid)
                        ├─ Create Secrets/ConfigMaps for credentials
                        ├─ Ensure squid-proxy Deployment + Service exists
                        ├─ Create NetworkPolicies (egress deny + allow proxy)
                        ├─ Create paude session Pod
                        ├─ Wait for pod ready
                        ├─ Start file sync (DevSpace or oc rsync)
                        └─ Attach via oc exec + tmux new-session

User works in Claude Code...

User disconnects (Ctrl+b d OR network drop):
  ├─ tmux session continues running in pod
  ├─ file sync continues (if using DevSpace)
  └─ Pods remain running (both paude and squid-proxy)

User runs: paude --backend=openshift (or paude attach)
  ├─ Finds existing session
  └─ Attaches via oc exec + tmux attach

User runs: paude stop
  ├─ Final sync (ensure all changes pushed)
  ├─ Delete paude session pod
  ├─ Cleanup session-specific secrets/configmaps
  └─ (squid-proxy stays running for future sessions)
```

**Note:** The squid-proxy pod is shared across sessions and managed as a Deployment.
NetworkPolicies are applied at namespace creation time and don't change per-session.

## Implementation Phases

### Phase 1: Backend Abstraction (Foundation)

**Goal:** Refactor existing Podman code into Backend interface without changing behavior.

**Scope:**
- Define `Backend` protocol in `src/paude/backends/base.py`
- Create `PodmanBackend` class wrapping existing functionality
- Update CLI to use backend abstraction
- Add `--backend` flag (default: "podman")
- All existing tests pass

**No user-facing changes** - pure refactoring.

### Phase 2: OpenShift Connectivity (MVP)

**Goal:** Basic OpenShift session that user can connect to.

**Scope:**
- Create `OpenShiftBackend` class
- Implement `start_session()` - create Pod with paude container
- Implement `attach_session()` - oc exec with TTY
- Implement `stop_session()` - delete Pod
- Implement `list_sessions()` - list Pods in namespace
- Manual image push (user pushes to registry beforehand)
- No file sync yet (use oc rsync manually)
- No tmux yet (session lost on disconnect)

**User experience:**
```bash
# One-time: push image
podman push paude:latest $REGISTRY/paude/paude:latest

# Start session
paude --backend=openshift
# Claude starts in pod, but session lost on disconnect
```

### Phase 3: Session Persistence (tmux)

**Goal:** Sessions survive network interruptions.

**Scope:**
- Add tmux to container image
- Modify entrypoint to use tmux
- Update `attach_session()` to use tmux attach
- Add `paude attach` command
- Add `paude sessions` command
- Session detection (find existing session on `paude` invocation)

**User experience:**
```bash
paude --backend=openshift
# Claude runs inside tmux
# Ctrl+b d detaches
# Network drop - session continues

paude attach
# Reconnects to existing session
```

### Phase 4: Credential Management

**Goal:** Automatic credential injection into OpenShift pods.

**Scope:**
- Create Secrets from local gcloud credentials
- Create ConfigMap from .gitconfig
- Mount credentials into pod
- Handle credential refresh/rotation
- Add `paude setup --backend=openshift` for initial setup

**User experience:**
```bash
# One-time setup
paude setup --backend=openshift
# Creates secrets from ~/.config/gcloud, ~/.gitconfig

paude --backend=openshift
# Credentials automatically available
```

### Phase 5: Automatic Image Push

**Goal:** User doesn't need to manually push images.

**Scope:**
- Detect OpenShift internal registry
- Login to registry using oc token
- Push images automatically when needed
- Cache images (only push when changed)
- Support custom image (from devcontainer.json)

**User experience:**
```bash
paude --backend=openshift
# Image pushed automatically if needed
Building workspace image...
Pushing to registry...
Starting session...
```

### Phase 6: File Synchronization

**Goal:** Real-time bidirectional file sync.

**Note:** Mutagen was originally considered but lacks native Kubernetes transport.
DevSpace sync is recommended; oc rsync provides a simpler fallback.

**Scope:**
- Create sync abstraction supporting multiple backends
- Implement DevSpace sync wrapper (preferred)
- Implement oc rsync fallback (simpler, built-in)
- Start sync on session start, stop on session stop
- Handle sync conflicts (local wins by default)
- Add `paude sync` command for manual sync trigger
- Exclude patterns (.git/objects, .venv, node_modules)

**DevSpace sync approach:**
- Use `devspace sync` standalone command
- Client-only, injects helper via kubectl cp
- Bidirectional with file watching
- Requires user to install devspace CLI

**oc rsync fallback:**
- Built into oc CLI (no extra install)
- One-way only; run two processes for bidirectional
- Use `--watch` for continuous sync
- Simpler but less robust than DevSpace

**User experience:**
```bash
paude --backend=openshift
# Files sync automatically (DevSpace or oc rsync)
# Edit locally, changes appear in pod
# Git commit in pod, changes sync back

paude sync --status
# Shows sync status
```

### Phase 7: Network Filtering

**Goal:** Same DNS-based filtering as local Podman setup.

**Scope:**
- Deploy squid proxy as separate Deployment + Service (not sidecar)
- Create NetworkPolicies to restrict paude pod egress to only squid-proxy Service
- Allow DNS resolution (kube-dns/openshift-dns)
- Route container traffic through proxy via HTTP_PROXY env vars
- Add EgressFirewall as defense-in-depth (OVN-Kubernetes only)
- Support `--allow-network` flag (skips NetworkPolicy creation)

**Why separate pods (not sidecar):**
- Kubernetes NetworkPolicy operates at pod level, not container level
- Sidecar shares network namespace with main container
- Cannot restrict main container egress while allowing sidecar egress
- Separate pods allow NetworkPolicy to differentiate between paude and squid

**Resources created:**
- `Deployment/squid-proxy` - Long-lived proxy (1 replica)
- `Service/squid-proxy` - ClusterIP service on port 3128
- `NetworkPolicy/paude-deny-all-egress` - Default deny for workload pods
- `NetworkPolicy/paude-allow-dns-and-proxy` - Allow DNS + proxy access
- (Optional) `EgressFirewall/paude-egress-firewall` - OVN-Kubernetes defense-in-depth

**User experience:**
```bash
paude --backend=openshift
# Network filtered (same as local)
# paude pod can only reach squid-proxy:3128 and DNS

paude --backend=openshift --allow-network
# Full network access (with warning)
# No NetworkPolicies created, no proxy configured
```

### Phase 8: Polish & Documentation

**Goal:** Production-ready with comprehensive docs.

**Scope:**
- Error handling and recovery
- Helpful error messages
- User documentation
- Troubleshooting guide
- Example configurations
- Performance optimization
- Resource limits configuration

## Configuration

### paude.json additions

```json
{
  "backend": {
    "type": "openshift",
    "context": "my-cluster",
    "namespace": "paude",
    "registry": "image-registry.openshift-image-registry.svc:5000",
    "resources": {
      "requests": {
        "cpu": "1",
        "memory": "4Gi"
      },
      "limits": {
        "cpu": "4",
        "memory": "8Gi"
      }
    },
    "timeout": {
      "idle": "4h",
      "max": "24h"
    }
  }
}
```

### CLI flags

```bash
paude --backend=openshift          # Use OpenShift backend
paude --openshift-context=...      # Specific kubeconfig context
paude --openshift-namespace=...    # Target namespace
paude attach [session-id]          # Attach to session
paude sessions                     # List sessions
paude stop [session-id]            # Stop session
paude sync [--status|--force]      # Sync operations
```

## Security Considerations

### Threat Model (OpenShift)

| Threat | Mitigation |
|--------|------------|
| Data exfiltration via network | NetworkPolicy (deny all egress except proxy) + Squid DNS filtering + EgressFirewall |
| Direct internet access bypassing proxy | NetworkPolicy blocks all egress except squid-proxy Service |
| Credential theft | Read-only mounts, no SSH keys |
| Pod escape | Non-root user, restricted SCC |
| Unauthorized cluster access | RBAC, ServiceAccount permissions |
| Image tampering | Pull from trusted internal registry |
| Sync conflict abuse | Local-wins policy, conflict logging |
| NetworkPolicy bypass via DNS | DNS allowed only to kube-dns/openshift-dns namespaces |

### Network Security Architecture

The OpenShift backend uses a **two-pod design** to achieve network isolation equivalent to the Podman internal network:

1. **paude session pod**: No direct internet access
   - NetworkPolicy denies all egress except DNS and squid-proxy Service
   - Communicates with internet only via HTTP_PROXY=squid-proxy:3128

2. **squid-proxy pod**: Filtered internet access
   - DNS-based filtering (same squid.conf as local)
   - Only allows *.googleapis.com, *.google.com
   - Runs as separate Deployment (shared across sessions)

This is architecturally equivalent to the Podman setup where:
- paude container runs on internal network (no internet)
- squid container bridges internal network to internet with filtering

### RBAC Requirements

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: paude-user
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/exec", "pods/log"]
    verbs: ["get", "list", "create", "delete", "watch"]
  - apiGroups: [""]
    resources: ["secrets", "configmaps", "services"]
    verbs: ["get", "list", "create", "update", "delete"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "create", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "create", "update", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "create", "update", "delete"]
  # Optional: EgressFirewall for OVN-Kubernetes clusters
  - apiGroups: ["k8s.ovn.org"]
    resources: ["egressfirewalls"]
    verbs: ["get", "list", "create", "update", "delete"]
```

## Testing Strategy

### Unit Tests
- Backend interface implementations
- Pod spec generation
- Secret/ConfigMap creation
- Sync command building (DevSpace/oc rsync)

### Integration Tests
- Against local OpenShift (CRC)
- Session lifecycle
- Credential mounting
- Network filtering

### E2E Tests
- Full workflow on real cluster
- Multi-cluster testing
- Network interruption recovery

## Dependencies

| Dependency | Purpose | User Action Required |
|------------|---------|---------------------|
| oc CLI | OpenShift operations | Install if using OpenShift |
| devspace | File synchronization (preferred) | Install (brew install devspace) |
| tmux | Session persistence | Bundled in container |

**Note:** DevSpace is optional - oc rsync (built into oc CLI) can be used as a fallback,
though it requires running two processes for bidirectional sync.

## Rollback Plan

Each phase is independently deployable:
- Phase 1: No user impact (internal refactor)
- Phase 2-8: `--backend=podman` always works as fallback

## Success Metrics

| Metric | Target |
|--------|--------|
| Session start time | < 30 seconds (cached image) |
| Sync latency | < 2 seconds for small changes |
| Reconnect time | < 5 seconds |
| Network filter parity | 100% of local filtering works |

## Timeline Estimates

| Phase | Complexity | Dependencies |
|-------|-----------|--------------|
| Phase 1: Backend Abstraction | Medium | None |
| Phase 2: OpenShift Connectivity | Medium | Phase 1 |
| Phase 3: Session Persistence | Low | Phase 2 |
| Phase 4: Credential Management | Medium | Phase 2 |
| Phase 5: Automatic Image Push | Medium | Phase 2 |
| Phase 6: File Synchronization | High | Phase 2 |
| Phase 7: Network Filtering | Medium | Phase 2 |
| Phase 8: Polish | Medium | All above |

Phases 2-5 and 6-7 can be parallelized.

## Open Questions for Future Work

1. **Workload Identity**: Support GKE/EKS/ARO workload identity instead of secret mounts?
2. **GPU support**: How to request GPU resources for AI workloads?
3. **Team features**: Shared namespaces, session visibility, resource quotas?
4. **IDE integration**: VS Code Remote SSH to pod?
