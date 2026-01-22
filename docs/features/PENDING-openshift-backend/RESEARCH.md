# OpenShift Backend Research

## Overview

This document captures research findings for adding OpenShift/Kubernetes support to paude, enabling users to run Claude Code in remote containers while editing code locally.

## User Requirements (from stakeholder interview)

| Requirement | Answer |
|-------------|--------|
| Target environments | Multiple OpenShift clusters (CRC, self-managed, cloud) |
| Image builds | Local builds + push to OpenShift internal registry |
| File sync | Real-time bidirectional (mutagen) |
| Credentials | Vertex AI + Git identity |
| Session persistence | Critical - must reconnect after laptop sleep/network drop |
| Network filtering | Squid sidecar (same DNS-based filtering as local) |
| Mutagen dependency | Acceptable to require installation |

## Research Areas

### 1. File Synchronization

**Recommendation: Mutagen**

| Solution | Bidirectional | K8s Native | Git Safe | Maintained | License |
|----------|---------------|------------|----------|------------|---------|
| **Mutagen** | Yes (4 modes) | Planned | With care | Yes (Docker) | MIT |
| rsync | No (needs wrapper) | Via wrapper | Risky | Yes | GPL v3 |
| ksync | Yes (Syncthing) | Yes | Problematic | No (archived) | Apache 2.0 |
| lsyncd | No | No | Risky | No | GPL v2+ |
| DevPod | N/A (remote edit) | Yes | Safe | Yes | MPL-2.0 |

**Mutagen key features:**
- Bidirectional sync with `two-way-safe` mode (conflicts preserved)
- Uses system OpenSSH for security
- Delta transfers minimize bandwidth
- MIT license, actively maintained by Docker

**Integration approach:**
- Mutagen requires an agent in the container (small Go binary)
- Transport over kubectl exec or SSH tunnel
- Session lifecycle managed by paude CLI

**Caveats:**
- No native kubectl transport yet (use SSH or custom wrapper)
- Git directory sync needs `two-way-resolved` mode with local priority

### 2. Session Management

**Recommendation: tmux inside container**

| Approach | Interactive | Reconnectable | Survives Pod Restart |
|----------|-------------|---------------|---------------------|
| **tmux in container** | Yes | Yes | No (unless PVC) |
| kubectl exec (raw) | Yes | No | N/A |
| Mosh + tmux | Yes | Yes (auto) | No |
| Web terminal (ttyd) | Yes | Browser-based | No |

**Why tmux:**
- Claude Code requires TTY for Ink-based UI
- tmux sessions survive network drops
- User can detach (`Ctrl+b d`) and reattach later
- Simple to implement, well-understood technology

**Implementation:**
```bash
# Container entrypoint
if tmux has-session -t claude 2>/dev/null; then
    tmux attach -t claude
else
    tmux new-session -s claude "claude $@"
fi
```

**kubectl exec limitations:**
- Connections drop after 4-5 hours of inactivity
- Network interruption kills session immediately
- No built-in reconnection mechanism
- tmux mitigates all these issues

### 3. OpenShift-Specific Considerations

#### Security Context Constraints (SCCs)

| SCC | Description | Paude Compatibility |
|-----|-------------|---------------------|
| `restricted` (default) | Random high UID, drop capabilities | Compatible if container handles arbitrary UIDs |
| `anyuid` | Run with Dockerfile UID | May be needed for file permissions |
| `privileged` | Full host access | Not needed, avoid |

**Recommendation:** Design container to work with `restricted` SCC. If file permission issues arise, request `anyuid` for the service account.

#### Routes vs Ingress

Not needed for paude - connections use `oc exec`/`oc rsh` rather than HTTP.

#### oc CLI vs kubectl

Use `oc` as primary CLI - it's a superset of kubectl with OpenShift-specific commands:
- `oc login` - Authentication
- `oc new-project` - Project creation
- `oc rsh` - Remote shell wrapper

### 4. Image Registry Strategy

**Chosen: OpenShift Internal Registry**

```bash
# One-time setup (requires admin)
oc patch configs.imageregistry.operator.openshift.io/cluster \
  --patch '{"spec":{"defaultRoute":true}}' --type=merge

# Get registry URL
REGISTRY=$(oc get route default-route -n openshift-image-registry \
  -o jsonpath='{.spec.host}')

# Login
podman login --tls-verify=false -u unused -p $(oc whoami -t) $REGISTRY

# Build and push
podman build -t paude:latest containers/paude/
podman tag paude:latest $REGISTRY/namespace/paude:latest
podman push --tls-verify=false $REGISTRY/namespace/paude:latest
```

**Advantages:**
- No external registry dependency
- Images cached in cluster
- Uses existing OpenShift auth

### 5. Credential Injection

| Credential | Local (Podman) | OpenShift |
|------------|----------------|-----------|
| gcloud ADC | Mount ~/.config/gcloud | Secret mount |
| Git config | Mount ~/.gitconfig | ConfigMap mount |
| Claude config | Copy ~/.claude | Secret mount |

**Implementation:**
```yaml
volumes:
  - name: gcloud-creds
    secret:
      secretName: paude-gcloud
  - name: git-config
    configMap:
      name: paude-gitconfig
volumeMounts:
  - name: gcloud-creds
    mountPath: /home/paude/.config/gcloud
    readOnly: true
  - name: git-config
    mountPath: /home/paude/.gitconfig
    subPath: .gitconfig
    readOnly: true
```

### 6. Network Security and Egress Filtering

#### The Problem: Podman vs OpenShift Network Models

In the local Podman setup, network isolation is achieved through:
1. An **internal network** (`podman network create --internal paude-internal`) with no external connectivity
2. **Two separate containers**: paude (no internet) and squid (bridges to internet)
3. Paude can only reach the internet via the squid proxy on the internal network

In OpenShift/Kubernetes, this model doesn't translate directly:
- Containers in the same Pod share a network namespace (including localhost)
- NetworkPolicy operates at the **pod level**, not per-container
- A sidecar container cannot have different network access than the main container
- Pods have full network access by default

#### Solution: Separate Pods with NetworkPolicy

**Recommendation: Deploy paude and squid as separate Pods with NetworkPolicy**

This is the only way to achieve equivalent security to the Podman setup:

| Approach | Network Isolation | Complexity | Podman Parity |
|----------|------------------|------------|---------------|
| **Separate Pods + NetworkPolicy** | Yes (L3/L4) | Medium | Full |
| Sidecar (same pod) | No | Low | None |
| Sidecar + iptables (CAP_NET_ADMIN) | Partial | High | Partial |
| EgressFirewall only | Namespace-level | Low | Partial |

**Why separate pods are required:**
- NetworkPolicy can deny egress from the paude pod to all destinations except the squid pod
- NetworkPolicy can allow the squid pod to reach external googleapis.com endpoints
- This mirrors the Podman internal network model at the Kubernetes layer

#### Architecture: Two-Pod Design

```
┌─────────────────────────────────────────────────────────────────┐
│  Namespace: paude-<user>                                        │
│                                                                 │
│  ┌─────────────────────┐         ┌─────────────────────┐       │
│  │ Pod: paude-session  │         │ Pod: squid-proxy    │       │
│  │                     │         │                     │       │
│  │ Labels:             │         │ Labels:             │       │
│  │   app: paude        │         │   app: squid-proxy  │       │
│  │   role: workload    │         │   role: proxy       │       │
│  │                     │         │                     │       │
│  │ ┌─────────────────┐ │  HTTP   │ ┌─────────────────┐ │       │
│  │ │ Claude Code     │─┼────────▶│ │ Squid Proxy     │─┼──────▶│ Internet
│  │ │                 │ │ :3128   │ │                 │ │       │ (googleapis.com)
│  │ │ HTTP_PROXY=     │ │         │ │ DNS filtering:  │ │       │
│  │ │ squid-proxy:3128│ │         │ │ *.googleapis.com│ │       │
│  │ └─────────────────┘ │         │ └─────────────────┘ │       │
│  │                     │         │                     │       │
│  │ Network: DENY ALL   │         │ Network: ALLOW      │       │
│  │ except squid-proxy  │         │ googleapis.com      │       │
│  └─────────────────────┘         └─────────────────────┘       │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Service: squid-proxy                                         ││
│  │ ClusterIP:3128 → squid-proxy pod                            ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ NetworkPolicy: paude-egress-deny                             ││
│  │ - Applies to: app=paude, role=workload                       ││
│  │ - Denies all egress except:                                  ││
│  │   - DNS (kube-dns, port 53)                                  ││
│  │   - squid-proxy service (port 3128)                          ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ NetworkPolicy: squid-egress-allow (optional defense-in-depth)││
│  │ - Applies to: app=squid-proxy                                ││
│  │ - Allows egress to: 0.0.0.0/0:443 (HTTPS)                    ││
│  │ - Note: DNS-based filtering done by Squid, not NetworkPolicy ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### NetworkPolicy Specifications

**1. Default Deny for paude workload:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: paude-deny-all-egress
  namespace: paude
spec:
  podSelector:
    matchLabels:
      app: paude
      role: workload
  policyTypes:
    - Egress
  egress: []  # Empty = deny all
```

**2. Allow DNS and proxy access only:**

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: paude-allow-dns-and-proxy
  namespace: paude
spec:
  podSelector:
    matchLabels:
      app: paude
      role: workload
  policyTypes:
    - Egress
  egress:
    # Allow DNS resolution
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: openshift-dns
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # Allow access to squid proxy only
    - to:
        - podSelector:
            matchLabels:
              app: squid-proxy
      ports:
        - protocol: TCP
          port: 3128
```

**3. (Optional) EgressFirewall as defense-in-depth (OVN-Kubernetes only):**

```yaml
apiVersion: k8s.ovn.org/v1
kind: EgressFirewall
metadata:
  name: paude-egress-firewall
  namespace: paude
spec:
  egress:
    - type: Allow
      to:
        dnsName: "*.googleapis.com"
    - type: Allow
      to:
        dnsName: "*.google.com"
    - type: Deny
      to:
        cidrSelector: 0.0.0.0/0
```

#### Squid Proxy Service

The squid pod needs a Kubernetes Service for stable DNS resolution:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: squid-proxy
  namespace: paude
spec:
  selector:
    app: squid-proxy
  ports:
    - protocol: TCP
      port: 3128
      targetPort: 3128
```

The paude container connects to `http://squid-proxy:3128` instead of `localhost:3128`.

#### Implications of Two-Pod Design

| Aspect | Single Pod (Sidecar) | Two Pods (Recommended) |
|--------|---------------------|------------------------|
| Network isolation | None | Full (via NetworkPolicy) |
| Lifecycle coupling | Tight (same pod) | Loose (independent pods) |
| Resource scaling | Shared | Independent |
| Failure isolation | Shared fate | Independent |
| Communication | localhost | Service DNS |
| Complexity | Lower | Medium |
| Session management | Simple | Needs coordination |

**Session coordination approach:**
- Squid proxy pod is long-lived (Deployment with 1 replica)
- Paude session pod is per-session (created/deleted per user session)
- Both share the namespace for NetworkPolicy scoping

#### Alternative: Transparent Proxy with CAP_NET_ADMIN

A sidecar could use iptables to redirect all traffic through itself:

```bash
# In init container or sidecar with CAP_NET_ADMIN
iptables -t nat -A OUTPUT -p tcp --dport 443 -j REDIRECT --to-port 3128
iptables -t nat -A OUTPUT -p tcp --dport 80 -j REDIRECT --to-port 3128
```

**Why we reject this approach:**
- Requires `CAP_NET_ADMIN` capability (security concern)
- May not work with `restricted` SCC
- Complex debugging when things go wrong
- Traffic can still bypass if container gains CAP_NET_ADMIN
- Not equivalent to Podman's network-level isolation

#### References

- [Kubernetes Network Policies](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Kubernetes Network Policy Recipes - Deny External Egress](https://github.com/ahmetb/kubernetes-network-policy-recipes/blob/master/14-deny-external-egress-traffic.md)
- [Red Hat Guide to Kubernetes Egress Network Policies](https://www.redhat.com/en/blog/guide-to-kubernetes-egress-network-policies)
- [OVN-Kubernetes EgressFirewall](https://docs.openshift.com/container-platform/4.14/networking/openshift_network_security/egress_firewall/configuring-egress-firewall-ovn.html)

### 7. Storage Strategy

For workspace files in OpenShift, two options:

**Option A: PVC + Mutagen (Recommended)**
- Create PVC for /workspace in pod
- Mutagen syncs local code to PVC
- Bidirectional sync handles git commits

**Option B: EmptyDir + Mutagen**
- Workspace is ephemeral (lost on pod restart)
- Simpler setup, suitable for short sessions
- Must sync before pod termination

## Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│  Developer Workstation                                              │
│                                                                     │
│  ┌─────────────┐     ┌──────────────┐     ┌──────────────────────┐ │
│  │ paude CLI   │────▶│ Mutagen      │────▶│ ~/.config/gcloud     │ │
│  │             │     │ (sync daemon)│     │ ~/.gitconfig         │ │
│  │ - start     │     └──────────────┘     │ ~/project/           │ │
│  │ - attach    │              │           └──────────────────────┘ │
│  │ - sync      │              │                                    │
│  │ - stop      │              │                                    │
│  └──────┬──────┘              │                                    │
│         │                     │                                    │
└─────────┼─────────────────────┼────────────────────────────────────┘
          │                     │
          │ oc exec             │ mutagen over kubectl
          │ (attach to tmux)    │ (bidirectional file sync)
          │                     │
          ▼                     ▼
┌────────────────────────────────────────────────────────────────────┐
│  OpenShift Cluster                                                  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Namespace: paude-<user>                                      │  │
│  │                                                               │  │
│  │  ┌───────────────────────────────┐  ┌──────────────────────┐ │  │
│  │  │ Pod: paude-session-abc123     │  │ Pod: squid-proxy     │ │  │
│  │  │ Labels: app=paude,role=workload  │ Labels: app=squid-proxy│ │  │
│  │  │                               │  │                      │ │  │
│  │  │ ┌───────────────────────────┐ │  │ ┌──────────────────┐ │ │  │
│  │  │ │ Container: paude          │ │  │ │ Container: squid │ │ │  │
│  │  │ │                           │ │  │ │                  │ │ │  │
│  │  │ │ ┌───────────────────────┐ │ │  │ │ DNS-based filter:│ │ │  │
│  │  │ │ │ tmux session "claude" │ │ │  │ │ *.googleapis.com │ │ │  │
│  │  │ │ │                       │ │ │  │ │ *.google.com     │ │ │  │
│  │  │ │ │ Claude Code CLI       │─┼─┼──┼▶│                  │─┼─┼──▶ Internet
│  │  │ │ │                       │ │ │  │ │ Port 3128        │ │ │  │
│  │  │ │ │ HTTP_PROXY=           │ │ │  │ └──────────────────┘ │ │  │
│  │  │ │ │ squid-proxy:3128      │ │ │  │                      │ │  │
│  │  │ │ └───────────────────────┘ │ │  └──────────────────────┘ │  │
│  │  │ │                           │ │              ▲            │  │
│  │  │ │ /workspace (PVC mount)    │ │              │            │  │
│  │  │ └───────────────────────────┘ │   ┌──────────┴─────────┐  │  │
│  │  │                               │   │ Service:           │  │  │
│  │  │ NetworkPolicy: DENY ALL       │   │ squid-proxy:3128   │  │  │
│  │  │ EXCEPT: DNS + squid-proxy svc │   └────────────────────┘  │  │
│  │  └───────────────────────────────┘                           │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ NetworkPolicy: paude-allow-dns-and-proxy                 │ │  │
│  │  │ - podSelector: app=paude, role=workload                  │ │  │
│  │  │ - egress: [DNS ports 53, squid-proxy:3128]               │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────────┐    │  │
│  │  │ Secret:         │  │ ConfigMap:                      │    │  │
│  │  │ gcloud-creds    │  │ git-config                      │    │  │
│  │  └─────────────────┘  └─────────────────────────────────┘    │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  openshift-image-registry                                     │  │
│  │  paude:latest, paude-proxy:latest                             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

**Key architectural change from original sidecar design:** The paude and squid containers now run in **separate pods** to enable NetworkPolicy-based egress filtering. This mirrors the Podman setup where containers run on an internal network with no direct internet access. See "Network Security and Egress Filtering" section for details.

## Key Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| File sync | Mutagen | Best bidirectional sync, MIT license, actively maintained |
| Session management | tmux | Simple, reliable, survives network drops |
| Network filtering | Separate pods + NetworkPolicy | Required for Podman-equivalent isolation; sidecar shares network namespace |
| Proxy deployment | Squid in separate pod + Service | NetworkPolicy can restrict paude pod to only reach squid Service |
| Image registry | OpenShift internal | No external dependency, uses existing auth |
| Storage | PVC | Persistent workspace across pod restarts |
| UID handling | Support arbitrary UIDs | Compatible with restricted SCC |

## Open Questions

1. **Mutagen transport**: Use SSH tunnel to pod or custom kubectl transport wrapper?
2. **Session timeout**: How long should idle pods run before auto-cleanup?
3. **Multi-cluster support**: How to handle kubeconfig context switching?
4. **Resource limits**: Default CPU/memory for paude pods?

## References

- [Mutagen Documentation](https://mutagen.io/documentation/)
- [tmux Manual](https://github.com/tmux/tmux/wiki)
- [OpenShift SCCs](https://docs.openshift.com/container-platform/4.14/authentication/managing-security-context-constraints.html)
- [OVN-Kubernetes EgressFirewall](https://docs.openshift.com/container-platform/4.14/networking/openshift_network_security/egress_firewall/configuring-egress-firewall-ovn.html)
- [OpenShift Internal Registry](https://docs.openshift.com/container-platform/4.14/registry/accessing-the-registry.html)
