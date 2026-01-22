# OpenShift Backend for Paude

## Overview

This feature adds support for running Claude Code on OpenShift/Kubernetes clusters, enabling:

- **Remote execution**: Run Claude on powerful cluster resources
- **Session persistence**: Disconnect and reconnect without losing work
- **Real-time file sync**: Edit locally, changes appear in cluster instantly
- **Security parity**: Same network filtering as local Podman

## Status

**PENDING** - Implementation not started

## Documents

| Document | Description |
|----------|-------------|
| [RESEARCH.md](RESEARCH.md) | Research findings on file sync, session management, OpenShift specifics |
| [PLAN.md](PLAN.md) | Architecture design, phased implementation approach |
| [TASKS.md](TASKS.md) | Detailed tasks with acceptance criteria for implementation |

## Quick Summary

### User Experience (Target)

```bash
# One-time setup
paude setup --backend=openshift

# Start session (or reconnect to existing)
paude --backend=openshift

# Claude runs in OpenShift pod
# Files sync automatically via mutagen
# Session persists via tmux

# Disconnect (Ctrl+b d)
# Laptop can sleep, network can drop

# Reconnect later
paude attach

# List sessions
paude sessions

# Stop session
paude stop
```

### Architecture

```
Local Machine                    OpenShift Cluster
┌──────────────┐                ┌─────────────────────┐
│ paude CLI    │                │ Pod                 │
│              │◀── oc exec ───▶│ ├─ paude container  │
│ ~/project/   │                │ │  └─ tmux          │
│    ↕         │                │ │     └─ claude     │
│ mutagen      │◀── sync ──────▶│ │        /workspace │
│              │                │ └─ squid sidecar    │
└──────────────┘                └─────────────────────┘
```

### Key Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| File sync | Mutagen | Bidirectional, conflict handling, MIT license |
| Session persistence | tmux | Survives network drops, simple, reliable |
| Network filtering | Squid sidecar | Same as local, DNS-based filtering |
| Image registry | OpenShift internal | No external dependency |

### Implementation Phases

1. **Backend Abstraction** - Refactor for pluggable backends
2. **OpenShift Connectivity** - Basic pod creation and exec
3. **Session Persistence** - tmux integration
4. **Credential Management** - Secrets for gcloud, git
5. **Automatic Image Push** - Push to internal registry
6. **File Synchronization** - Mutagen integration
7. **Network Filtering** - Squid sidecar + EgressFirewall
8. **Polish** - Error handling, docs, configuration

### Dependencies

| Dependency | Purpose | Install |
|------------|---------|---------|
| oc CLI | OpenShift operations | From cluster or brew |
| mutagen | File synchronization | `brew install mutagen` |
| tmux | Session persistence | Bundled in container |

## Verification Checklist

After implementation:

- [ ] `paude --backend=openshift` starts session on cluster
- [ ] Files sync bidirectionally with low latency (<2s)
- [ ] Session survives `Ctrl+b d` (detach)
- [ ] Session survives network interruption
- [ ] `paude attach` reconnects to existing session
- [ ] Network filtering blocks non-allowed domains
- [ ] Git operations work (commit, status, diff)
- [ ] Vertex AI authentication works
- [ ] `paude stop` cleans up all resources
- [ ] Documentation complete and accurate

## References

- [Mutagen Documentation](https://mutagen.io/documentation/)
- [OpenShift Container Platform Docs](https://docs.openshift.com/)
- [tmux Wiki](https://github.com/tmux/tmux/wiki)
- [Paude Roadmap](../../ROADMAP.md) - Theme 3: Backend Abstraction
