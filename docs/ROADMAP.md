# Paude Roadmap

## Vision

Using `paude` in a repository feels just like using `claude`, but:
- **Secure by default** - Network filtering, credential protection, config isolation
- **Flexible execution** - Local container or remote Kubernetes/OpenShift
- **Zero friction** - One-time setup, then seamless daily use

---

## Current State (v0.4.x)

- Python implementation with Typer CLI
- Podman-based container execution
- Squid proxy for network filtering (hardcoded allowlist)
- Vertex AI authentication via gcloud ADC
- devcontainer.json/paude.json configuration support
- venv isolation for Python projects

---

## Roadmap Themes

### Theme 1: Security Hardening

Close security gaps in the current implementation.

### Theme 2: Configuration Flexibility

Let users customize security policy without code changes.

### Theme 3: Backend Abstraction

Support multiple execution backends (local, Kubernetes, etc).

### Theme 4: Session Management

Enable long-running tasks with disconnect/reconnect.

### Theme 5: Enterprise Features

Multi-user, audit, compliance for organizational use.

---

## Detailed Roadmap Items

### 1. Claude Config Isolation

**Priority: High (Security)**
**Theme: Security Hardening**
**Status: Research needed**

#### Problem

Currently, `~/.claude` is copied into the container at startup. However:
- Changes Claude makes to `.claude/` in the project directory persist to host
- A malicious project could modify allowed tools, MCP servers, or trust settings
- These changes take effect next time someone runs Claude locally

#### Current Behavior

```
Host                          Container
~/.claude ──copy──►  /tmp/claude.seed ──copy──► ~/.claude
                                                (writable)

project/.claude ◄──rw mount──► project/.claude
                    ↑
                    Problem: changes persist to host
```

#### Proposed Solution

```
Host                          Container
~/.claude ──copy──►  ~/.claude (container-local, ephemeral)

project/.claude ──ro mount──► /tmp/claude.project.seed
                              ↓ copy
                              project/.claude (tmpfs, ephemeral)
```

Options:
1. **Shadow with tmpfs** - Like venv isolation, shadow project/.claude with tmpfs
2. **Read-only mount** - Mount project/.claude as read-only, copy to writable location
3. **Configurable** - Let user choose isolation level via paude.json

#### Considerations

- Some .claude changes might be intentional (user adds MCP server during session)
- Need mechanism to "commit" changes back to host if desired
- Breaking change for users who expect persistence

#### Acceptance Criteria

- [ ] Project .claude changes don't persist by default
- [ ] User can opt-in to persistence
- [ ] Clear documentation on isolation model
- [ ] `paude --sync-claude-config` to manually sync changes

---

### 2. Configurable Network Allowlists

**Priority: High (Usability)**
**Theme: Configuration Flexibility**
**Status: Designed (see config-layering feature)**

#### Problem

Current network filtering has a hardcoded allowlist:
- `*.googleapis.com`
- `*.google.com`

Users often need additional hosts:
- Package registries (pypi.org, npmjs.org, crates.io)
- Internal registries
- Documentation sites
- APIs their code calls

Currently, only option is `--allow-network` which disables all filtering.

#### Proposed Solution

paude.json network policy (from config-layering):

```json
{
  "network": {
    "mode": "restricted",
    "allowlist": [
      "pypi.org",
      "files.pythonhosted.org",
      "*.internal.company.com"
    ]
  }
}
```

Plus CLI override for session:
```bash
paude --network-allow="custom.api.com"
```

#### Implementation

- Dynamic squid.conf generation from allowlist
- Base domains always included (googleapis.com)
- Glob pattern support (*.domain.com)
- Union of global + project allowlist (with project-can-only-restrict option)

#### Acceptance Criteria

- [ ] Custom domains accessible when in allowlist
- [ ] Non-allowlist domains blocked
- [ ] Works with glob patterns
- [ ] CLI flag for session additions
- [ ] Documented in README

---

### 3. Backend Abstraction

**Priority: High (Architecture)**
**Theme: Backend Abstraction**
**Status: Research needed**

#### Problem

Paude is tightly coupled to Podman. Users want:
- **Kubernetes backend** - Run in remote cluster, persist across disconnects
- **Docker backend** - For users without Podman
- **Remote Podman** - Podman on a remote machine via SSH

#### Proposed Architecture

```
paude CLI
    │
    ▼
┌─────────────────────────────┐
│     Backend Interface       │
│  - start_session()          │
│  - stop_session()           │
│  - attach_session()         │
│  - list_sessions()          │
│  - sync_files()             │
└─────────────────────────────┘
    │           │           │
    ▼           ▼           ▼
┌───────┐  ┌────────┐  ┌────────────┐
│Podman │  │  K8s   │  │Remote      │
│(local)│  │        │  │Podman(SSH) │
└───────┘  └────────┘  └────────────┘
```

#### Backend Interface (Draft)

```python
class Backend(Protocol):
    def start_session(
        self,
        image: str,
        workspace: Path,
        env: dict[str, str],
        network_policy: NetworkPolicy,
    ) -> Session: ...

    def attach_session(self, session_id: str) -> int: ...
    def stop_session(self, session_id: str) -> None: ...
    def list_sessions(self) -> list[Session]: ...
    def sync_workspace(self, session_id: str, direction: str) -> None: ...
```

#### Podman Backend (Current, Refactored)

- Minimal changes, extract current logic into Backend interface
- Session = container ID
- sync_workspace = no-op (direct mount)

#### Kubernetes Backend (New)

Major challenges to solve:
- **Pod creation**: Generate Pod spec from paude config
- **Network policy**: K8s NetworkPolicy instead of squid sidecar? Or keep sidecar?
- **File sync**: No direct mounts; need sync mechanism
- **Credential injection**: Workload Identity, Secrets, or ADC forwarding?
- **Session persistence**: Pod stays running after disconnect
- **Reconnection**: Attach to existing pod's terminal

See [Kubernetes Backend Research](#kubernetes-backend-deep-dive) below.

#### Acceptance Criteria

- [ ] Backend interface defined
- [ ] Podman backend implements interface
- [ ] `--backend` flag to select backend
- [ ] Backend auto-detection (Podman available? K8s config present?)

---

### 4. Kubernetes Backend Deep Dive

**Priority: Medium (Strategic)**
**Theme: Backend Abstraction**
**Status: Research needed**

#### Use Cases

1. **Long-running tasks** - Start refactoring, disconnect laptop, reconnect later
2. **Powerful hardware** - Use cluster resources (GPU, more RAM)
3. **Consistent environment** - Same environment for whole team
4. **Air-gapped networks** - Cluster has network access laptop doesn't

#### Architecture Options

##### Option A: Pod with File Sync

```
┌──────────────────────────────────────────────┐
│  Kubernetes Cluster                          │
│  ┌────────────────────────────────────────┐  │
│  │ Pod: paude-session-abc123              │  │
│  │  ┌─────────────┐  ┌─────────────────┐  │  │
│  │  │ paude       │  │ squid proxy     │  │  │
│  │  │ container   │  │ (sidecar)       │  │  │
│  │  │             │  │                 │  │  │
│  │  │ /workspace ◄┼──┼─ file sync ─────┼──┼──┼── Local machine
│  │  │             │  │                 │  │  │
│  │  └─────────────┘  └─────────────────┘  │  │
│  └────────────────────────────────────────┘  │
│  NetworkPolicy: egress to allowlist only     │
└──────────────────────────────────────────────┘
```

File sync options:
- **mutagen** - Fast, bidirectional, handles conflicts
- **rsync** - Simple, one-way, might be enough
- **lsyncd** - Daemon-based, real-time
- **Custom** - Git-based (commit changes, push/pull)

##### Option B: PVC with Git Sync

```
┌──────────────────────────────────────────────┐
│  Kubernetes Cluster                          │
│  ┌────────────────────────────────────────┐  │
│  │ Pod: paude-session-abc123              │  │
│  │  ┌─────────────┐  ┌─────────────────┐  │  │
│  │  │ paude       │  │ git-sync        │  │  │
│  │  │ container   │  │ (sidecar)       │  │  │
│  │  │             │  │                 │  │  │
│  │  │ /workspace ◄┼──┤ PVC             │  │  │
│  │  │             │  │                 │  │  │
│  │  └─────────────┘  └─────────────────┘  │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

User workflow:
1. `paude --backend=k8s` starts session, clones repo to PVC
2. Claude makes changes
3. User runs `paude sync` to see changes locally
4. Or: Claude commits changes, user pulls

##### Option C: DevPod Integration

Use [DevPod](https://devpod.sh/) as the Kubernetes orchestration layer:

```bash
# DevPod handles K8s complexity
devpod up --provider kubernetes --ide none

# Paude attaches with security overlay
paude --attach=devpod://workspace-name
```

Pros:
- DevPod already solves K8s dev environment problem
- File sync, reconnection, multi-cluster support built-in

Cons:
- Another dependency
- Need to inject security controls on top

#### Network Security in Kubernetes

Options:
1. **NetworkPolicy** - Native K8s, but limited to IP ranges (no DNS filtering)
2. **Squid sidecar** - Same as local, requires proxy env vars
3. **Service mesh** - Istio/Linkerd egress policies
4. **Egress gateway** - Centralized filtering

Recommendation: Start with squid sidecar (familiar), consider NetworkPolicy for IP-based filtering.

#### Credential Injection

| Credential | Local (Podman) | Kubernetes |
|------------|----------------|------------|
| gcloud ADC | Mount ~/.config/gcloud | Workload Identity or Secret mount |
| Git config | Mount ~/.gitconfig | ConfigMap or Secret |
| Claude config | Copy ~/.claude | Secret or ConfigMap |

Workload Identity preferred for GKE/EKS/AKS.

#### Session Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│  paude k8s session lifecycle                                │
│                                                             │
│  start ──► running ──► detached ──► reattached ──► stopped  │
│              │                          │                   │
│              │                          │                   │
│              └──────── timeout ─────────┘                   │
│                           │                                 │
│                           ▼                                 │
│                        stopped                              │
└─────────────────────────────────────────────────────────────┘
```

Commands:
- `paude` (in K8s mode) - Start new session or reattach to existing
- `paude sessions` - List active sessions
- `paude attach <session>` - Attach to specific session
- `paude stop <session>` - Stop and cleanup session
- `paude sync` - Sync files with running session

#### Open Questions

1. **Default file sync strategy?** Git-based vs real-time sync
2. **Session timeout?** How long before idle sessions stop
3. **Resource limits?** CPU/memory for pods
4. **Multi-cluster?** Context switching
5. **Namespace?** Dedicated namespace or user's choice

---

### 5. Config Layering

**Priority: High (Usability)**
**Theme: Configuration Flexibility**
**Status: Designed**

Already researched. See [docs/features/PENDING-config-layering/](features/PENDING-config-layering/).

Summary:
- devcontainer.json for container setup (reuse ecosystem)
- paude.json for security policy (network, credentials, etc)
- Restrictive layering (project can only narrow global policy)

---

### 6. Session Management (Local)

**Priority: Medium (Usability)**
**Theme: Session Management**
**Status: Not started**

#### Problem

Currently, paude runs interactively and exits when done. Users want:
- Background sessions
- Reconnection to crashed sessions
- Multiple parallel sessions

#### Proposed Features

```bash
# Start in background
paude --detach
# Session started: paude-abc123

# List sessions
paude sessions
# ID            STATUS    STARTED         WORKSPACE
# paude-abc123  running   2 mins ago      /home/user/project
# paude-def456  stopped   1 hour ago      /home/user/other

# Attach to session
paude attach abc123

# Stop session
paude stop abc123
```

#### Implementation

For Podman:
- Use `podman run -d` for detached
- Store session metadata in ~/.local/share/paude/sessions/
- Use `podman attach` for reconnection

#### Acceptance Criteria

- [ ] `--detach` starts background session
- [ ] `paude sessions` lists sessions
- [ ] `paude attach` reconnects
- [ ] `paude stop` cleans up
- [ ] Session metadata persisted

---

### 7. Audit Logging

**Priority: Medium (Enterprise)**
**Theme: Enterprise Features**
**Status: Designed (in config-layering)**

#### Use Cases

- Security teams want to review what Claude did
- Compliance requirements (SOC2, HIPAA)
- Debugging issues after the fact
- Cost attribution (which projects use most resources)

#### What to Log

| Event | Data |
|-------|------|
| Session start | User, workspace, effective policy, image |
| Session end | Duration, exit code |
| Command execution | Command, exit code, timing (via hook) |
| File modifications | Path, operation (create/modify/delete) |
| Network requests | Domain, allow/deny (from squid logs) |

#### Log Format

```json
{
  "timestamp": "2024-01-15T10:30:00Z",
  "session_id": "paude-abc123",
  "event": "command_executed",
  "data": {
    "command": "npm install",
    "exit_code": 0,
    "duration_ms": 5432
  }
}
```

#### Storage Options

- Local file (~/.local/share/paude/audit.log)
- Syslog
- Cloud logging (Stackdriver, CloudWatch)
- Webhook to SIEM

---

### 8. OpenTelemetry Integration

**Priority: Medium (Enterprise/Observability)**
**Theme: Enterprise Features**
**Status: Research needed**

#### Problem

Claude Code exports [OpenTelemetry metrics](https://docs.anthropic.com/en/docs/claude-code/monitoring) for usage tracking:
- Token consumption
- API latency
- Cost tracking
- Session duration

When running in Kubernetes, users want these metrics shipped to:
- MLflow for ML experiment tracking
- Prometheus/Grafana for infrastructure monitoring
- Datadog, New Relic, or other APM tools
- Custom OTEL collectors

Currently, paude doesn't configure or forward these metrics.

#### Claude Code OTEL Configuration

Claude Code supports OTEL via environment variables:

```bash
# Enable OTEL export
CLAUDE_CODE_ENABLE_TELEMETRY=1

# OTEL collector endpoint
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317

# Optional: service name, resource attributes
OTEL_SERVICE_NAME=paude-session
OTEL_RESOURCE_ATTRIBUTES=session.id=abc123,user=ben
```

#### Proposed Solution

##### Local (Podman)

```json
// paude.json
{
  "telemetry": {
    "otel": {
      "enabled": true,
      "endpoint": "http://localhost:4317",
      "service_name": "paude",
      "attributes": {
        "environment": "development"
      }
    }
  }
}
```

Paude sets the appropriate environment variables in the container.

##### Kubernetes

```json
// paude.json
{
  "telemetry": {
    "otel": {
      "enabled": true,
      "endpoint": "http://otel-collector.monitoring:4317",
      "service_name": "paude",
      "attributes": {
        "cluster": "production",
        "team": "platform"
      }
    }
  }
}
```

For K8s, additional considerations:
- Inject pod name, namespace as resource attributes
- Support for OTEL sidecar collector pattern
- mTLS for secure collector communication

#### Architecture

##### Option A: Direct Export

```
┌─────────────────────────────────────┐
│ Pod                                 │
│  ┌─────────────┐                    │
│  │ Claude Code │──OTLP──►  Remote Collector
│  │             │           (MLflow, etc)
│  └─────────────┘                    │
└─────────────────────────────────────┘
```

Simple, but requires network access to collector.

##### Option B: Sidecar Collector

```
┌─────────────────────────────────────┐
│ Pod                                 │
│  ┌─────────────┐  ┌──────────────┐  │
│  │ Claude Code │──►│ OTEL Sidecar │──► Remote Collector
│  │             │   │ Collector    │
│  └─────────────┘  └──────────────┘  │
└─────────────────────────────────────┘
```

Better for:
- Batching/buffering
- Adding pod metadata
- Secure forwarding with mTLS
- Sampling/filtering

##### Option C: DaemonSet Collector

```
┌─────────────────────────────────────┐
│ Node                                │
│  ┌─────────────┐                    │
│  │ Claude Code │──► localhost:4317  │
│  └─────────────┘        │           │
│                         ▼           │
│  ┌──────────────────────────────┐   │
│  │ OTEL Collector (DaemonSet)  │───► Remote
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

Cluster-level concern, not paude's responsibility to deploy.
Paude just needs to know the endpoint.

#### Metrics of Interest

From Claude Code's OTEL export:

| Metric | Type | Description |
|--------|------|-------------|
| `claude.tokens.input` | Counter | Input tokens consumed |
| `claude.tokens.output` | Counter | Output tokens generated |
| `claude.api.latency` | Histogram | API call duration |
| `claude.api.errors` | Counter | API error count |
| `claude.session.duration` | Gauge | Session length |
| `claude.tools.invocations` | Counter | Tool calls by type |

Custom attributes paude should add:
- `paude.session_id`
- `paude.workspace`
- `paude.backend` (podman/kubernetes)
- `paude.network_mode` (restricted/open)

#### MLflow Integration

For users wanting metrics in MLflow:

```json
{
  "telemetry": {
    "otel": {
      "enabled": true,
      "endpoint": "http://mlflow.internal:4317"
    },
    "mlflow": {
      "experiment": "claude-code-usage",
      "tracking_uri": "http://mlflow.internal:5000"
    }
  }
}
```

MLflow can ingest OTEL metrics, or paude could directly log to MLflow's tracking API for richer integration (run association, artifacts, etc).

#### Implementation Phases

1. **Phase 1**: Pass-through OTEL environment variables
   - User sets OTEL_* env vars, paude forwards to container
   - Zero paude logic, just env var passthrough

2. **Phase 2**: paude.json telemetry configuration
   - Parse telemetry config from paude.json
   - Generate OTEL_* env vars from config
   - Add paude-specific resource attributes

3. **Phase 3**: K8s sidecar support
   - Option to inject OTEL collector sidecar
   - Configure sidecar from paude.json
   - mTLS support

4. **Phase 4**: MLflow native integration (optional)
   - Direct MLflow tracking API integration
   - Associate metrics with MLflow runs
   - Artifact logging (session transcripts?)

#### Acceptance Criteria

- [ ] OTEL env vars forwarded to container
- [ ] paude.json telemetry config parsed
- [ ] Resource attributes include paude metadata
- [ ] Works with common collectors (Jaeger, Zipkin, OTLP)
- [ ] K8s sidecar pattern documented
- [ ] MLflow example in docs

---

### 10. Additional Cloud Provider Support

**Priority: Low (Expansion)**
**Theme: Configuration Flexibility**
**Status: Not started**

Currently Vertex AI only. Future:

| Provider | Authentication | Considerations |
|----------|----------------|----------------|
| AWS Bedrock | IAM credentials | ~/.aws mount, env vars |
| Azure OpenAI | Azure AD | ~/.azure mount |
| Anthropic Direct | API key | ANTHROPIC_API_KEY env |

#### Implementation

```json
// paude.json
{
  "provider": "bedrock",
  "credentials": {
    "aws": true
  }
}
```

Credential mounts and environment variables set based on provider.

---

### 11. Plugin Isolation

**Priority: Low (Security)**
**Theme: Security Hardening**
**Status: Research needed**

#### Problem

Claude plugins (MCP servers, custom tools) run with full container privileges. A malicious plugin could:
- Exfiltrate data via allowed network paths
- Access credentials
- Modify files

#### Possible Approaches

1. **Plugin allowlist** - Only run approved plugins
2. **Network isolation per plugin** - Separate proxy rules
3. **Filesystem isolation** - Plugin can only see specific paths
4. **Capability dropping** - Remove unnecessary Linux capabilities

#### Recommendation

Start with plugin allowlist in paude.json:

```json
{
  "plugins": {
    "allow": ["filesystem", "web-search"],
    "deny": ["*"]
  }
}
```

Advanced isolation is complex and may not be worth the effort for most users.

---

### 12. IDE Integration (Optional)

**Priority: Low (Convenience)**
**Theme: Enterprise Features**
**Status: Not started**

While paude is CLI-first, some users prefer IDE integration.

#### Approach

Don't build IDE plugins. Instead:
- Document how to configure VS Code to use paude as container backend
- Provide devcontainer.json that calls paude internally
- Let existing Dev Containers extension handle UI

```json
// .devcontainer/devcontainer.json
{
  "initializeCommand": "paude --backend=podman --detach",
  "attachCommand": "paude attach"
}
```

This keeps paude CLI-focused while enabling IDE workflows.

---

## Phased Implementation

### Phase 1: Foundation (Current + Next)

1. Claude config isolation (security gap)
2. Configurable network allowlists (quick win)
3. Config layering (devcontainer.json + paude.json)

**Goal**: Close security gaps, improve configuration

### Phase 2: Polish

4. Session management (local)
5. Audit logging basics
6. Documentation overhaul

**Goal**: Production-ready for local use

### Phase 3: Remote Execution

7. Backend abstraction
8. Kubernetes backend (basic)
9. File sync mechanism

**Goal**: Basic Kubernetes support working

### Phase 4: Enterprise

10. Kubernetes backend (advanced - Workload Identity, NetworkPolicy)
11. OpenTelemetry integration
12. Additional cloud providers
13. Advanced audit logging

**Goal**: Enterprise-ready

### Phase 5: Ecosystem

14. Plugin isolation
15. IDE integration docs
16. CI/CD integration

**Goal**: Complete ecosystem

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Security gaps closed | 0 known gaps |
| Config without code changes | 100% of common cases |
| Local vs K8s parity | Same user experience |
| Setup time | < 5 minutes for new project |
| Daily friction | `paude` feels like `claude` |
| Observability | OTEL metrics flow to standard collectors |

---

## Future Exploration

Ideas worth tracking but not prioritized for current development.

### Multi-Assistant Support

The core security model (network filtering, credential protection, container isolation) is generic and could support other CLI coding assistants:

- **Codex CLI** (OpenAI)
- **Aider** (open source, multi-model)
- **Amazon Q Developer CLI**
- **Future tools**

#### Minimal Viable Approach

Add `--shell` mode that provides the security envelope without assistant-specific logic:

```bash
paude --shell
# Now in isolated container with network filtering
$ pip install aider-chat
$ aider
```

#### Full Approach

Pluggable assistant configs:

```json
{
  "assistant": "aider",
  "auth": {
    "provider": "openai"
  }
}
```

With pre-built images, auth helpers, and flag mappings per tool.

#### Decision Criteria

- User demand for specific tools
- Competitive landscape changes
- Maintenance burden vs. user base expansion

#### Current Status

**Not prioritized.** Focus remains on Claude Code. Revisit if:
- Significant user requests for other tools
- Claude Code market position changes
- A contributor wants to champion this

---

## Not Planned

Things we're explicitly NOT doing:

1. **Building our own LLM runtime** - Use Claude as provided
2. **IDE-first approach** - CLI is primary interface
3. **Windows support** - Focus on Linux/macOS (WSL works)
4. **Docker Compose orchestration** - Keep it simple
5. **Multi-container workloads** - One container per session
6. **Custom model hosting** - Vertex AI / Bedrock / Direct only

---

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for how to contribute.

Feature proposals should include:
- Problem statement
- Proposed solution
- Security implications
- Acceptance criteria

Create docs in `docs/features/PENDING-<feature-name>/` following the RESEARCH/PLAN/TASKS pattern. After implementation, rename to `YYYY-MM-DD-<feature-name>/`.
