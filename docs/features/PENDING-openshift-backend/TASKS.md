# OpenShift Backend: Implementation Tasks

Each task includes acceptance criteria suitable for an Opus 4.5 or Sonnet 4.5 model to implement.

## Phase 1: Backend Abstraction

### Task 1.1: Define Backend Protocol

**File:** `src/paude/backends/base.py`

**Description:** Create the abstract Backend protocol that all container backends must implement.

**Acceptance Criteria:**
- [ ] Create `src/paude/backends/` directory with `__init__.py`
- [ ] Define `Session` dataclass with: `id`, `status`, `workspace`, `created_at`, `backend_type`
- [ ] Define `Backend` Protocol with methods:
  - `start_session(image, workspace, env, network_restricted) -> Session`
  - `attach_session(session_id) -> int`
  - `stop_session(session_id) -> None`
  - `list_sessions() -> list[Session]`
  - `sync_workspace(session_id, direction) -> None`
- [ ] Include docstrings explaining each method's contract
- [ ] Export `Backend` and `Session` from `__init__.py`

**Implementation Notes:**
- Use `typing.Protocol` for structural subtyping
- Keep the interface minimal - can extend later

---

### Task 1.2: Create Podman Backend

**File:** `src/paude/backends/podman.py`

**Description:** Wrap existing Podman functionality into the Backend interface.

**Acceptance Criteria:**
- [ ] Create `PodmanBackend` class implementing `Backend` protocol
- [ ] `start_session()` wraps existing `ContainerRunner.run_claude()`
  - For Podman, session is synchronous (blocks until exit)
  - Return Session with container ID
- [ ] `attach_session()` - for Podman, re-runs container (no persistence)
- [ ] `stop_session()` wraps `ContainerRunner.stop_container()`
- [ ] `list_sessions()` - returns empty list (Podman sessions are ephemeral)
- [ ] `sync_workspace()` - no-op for Podman (direct mounts)
- [ ] All existing functionality preserved

**Implementation Notes:**
- This is a refactoring task - behavior should not change
- `ImageManager`, `NetworkManager`, `ContainerRunner` stay as-is
- `PodmanBackend` composes these existing classes

---

### Task 1.3: Update CLI for Backend Selection

**File:** `src/paude/cli.py`

**Description:** Add `--backend` flag and route to appropriate backend.

**Acceptance Criteria:**
- [ ] Add `--backend` option with choices `["podman", "openshift"]`
- [ ] Default to `"podman"` for backward compatibility
- [ ] Error message if `openshift` selected but not implemented yet
- [ ] Update main() to instantiate correct backend
- [ ] Pass backend to execution logic
- [ ] Add `--openshift-context` flag (for future use)
- [ ] Add `--openshift-namespace` flag (for future use)
- [ ] Update help text

**Test Cases:**
- `paude` - uses podman (default)
- `paude --backend=podman` - explicit podman
- `paude --backend=openshift` - error until Phase 2

---

### Task 1.4: Add Backend Unit Tests

**File:** `tests/test_backends.py`

**Description:** Test the backend abstraction layer.

**Acceptance Criteria:**
- [ ] Test `Session` dataclass creation and fields
- [ ] Test `PodmanBackend` instantiation
- [ ] Test that `PodmanBackend` methods delegate correctly
- [ ] Mock subprocess calls to avoid container operations
- [ ] Test backend selection in CLI

---

## Phase 2: OpenShift Connectivity

### Task 2.1: Create OpenShift Backend Skeleton

**File:** `src/paude/backends/openshift.py`

**Description:** Create the OpenShift backend class with stub implementations.

**Acceptance Criteria:**
- [ ] Create `OpenShiftConfig` dataclass:
  - `context: str | None` (kubeconfig context)
  - `namespace: str` (default: "paude")
  - `registry: str | None` (auto-detect if None)
- [ ] Create `OpenShiftBackend` class implementing `Backend`
- [ ] Constructor takes `OpenShiftConfig`
- [ ] All methods raise `NotImplementedError` with helpful message
- [ ] Add helper method `_run_oc(*args)` for oc CLI calls

---

### Task 2.2: Implement oc CLI Wrapper

**File:** `src/paude/backends/openshift.py`

**Description:** Add helper methods for common oc operations.

**Acceptance Criteria:**
- [ ] `_run_oc(*args, capture=True)` - run oc command, return result
- [ ] `_check_connection()` - verify oc login status
- [ ] `_get_current_namespace()` - get active namespace
- [ ] `_ensure_namespace()` - create namespace if not exists
- [ ] `_get_registry_url()` - detect internal registry URL
- [ ] Handle oc not installed error gracefully
- [ ] Handle not logged in error gracefully

**Test Cases:**
- Mock subprocess to test command building
- Test error handling for missing oc
- Test error handling for not logged in

---

### Task 2.3: Implement Pod Spec Generation

**File:** `src/paude/backends/openshift.py`

**Description:** Generate Kubernetes Pod manifest for paude session.

**Acceptance Criteria:**
- [ ] Create `_generate_pod_spec(image, env, session_id)` method
- [ ] Pod spec includes:
  - Container name: "paude"
  - Image from parameter
  - Environment variables
  - Resource requests/limits (configurable)
  - Volume mounts for workspace (emptyDir initially)
  - SecurityContext: non-root, drop capabilities
  - Labels: `app=paude`, `session-id=<id>`
- [ ] Return as Python dict (convert to YAML for apply)
- [ ] Support for sidecar container (Phase 7)

**Pod Spec Example:**
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: paude-session-abc123
  labels:
    app: paude
    session-id: abc123
spec:
  containers:
    - name: paude
      image: image-registry.../paude:latest
      stdin: true
      tty: true
      env:
        - name: CLAUDE_CODE_USE_VERTEX
          value: "1"
      resources:
        requests:
          memory: "1Gi"
          cpu: "500m"
  restartPolicy: Never
```

---

### Task 2.4: Implement start_session()

**File:** `src/paude/backends/openshift.py`

**Description:** Create pod and wait for it to be ready.

**Acceptance Criteria:**
- [ ] Generate unique session ID (timestamp + random)
- [ ] Generate pod spec
- [ ] Apply pod with `oc apply -f -`
- [ ] Wait for pod to be Running (with timeout)
- [ ] Return Session object with pod name as ID
- [ ] Handle pod creation failures
- [ ] Handle timeout waiting for ready

**Implementation Notes:**
```python
def start_session(self, image, workspace, env, network_restricted=True):
    session_id = f"{int(time.time())}-{secrets.token_hex(4)}"
    pod_spec = self._generate_pod_spec(image, env, session_id)

    # Apply pod
    result = subprocess.run(
        ["oc", "apply", "-f", "-"],
        input=yaml.dump(pod_spec),
        text=True,
        capture_output=True,
    )

    # Wait for ready
    self._wait_for_pod_ready(f"paude-session-{session_id}")

    return Session(
        id=session_id,
        status="running",
        workspace=workspace,
        created_at=...,
        backend_type="openshift",
    )
```

---

### Task 2.5: Implement attach_session()

**File:** `src/paude/backends/openshift.py`

**Description:** Attach to running pod with interactive shell.

**Acceptance Criteria:**
- [ ] Find pod by session ID
- [ ] Verify pod is running
- [ ] Run `oc exec -it <pod> -- claude` (initially without tmux)
- [ ] Return exit code
- [ ] Handle pod not found
- [ ] Handle pod not running

**Implementation Notes:**
```python
def attach_session(self, session_id: str) -> int:
    pod_name = f"paude-session-{session_id}"

    result = subprocess.run(
        ["oc", "exec", "-it", pod_name, "--", "claude"],
    )
    return result.returncode
```

---

### Task 2.6: Implement stop_session()

**File:** `src/paude/backends/openshift.py`

**Description:** Delete pod and associated resources.

**Acceptance Criteria:**
- [ ] Delete pod: `oc delete pod <name> --grace-period=0`
- [ ] Handle pod not found (already deleted)
- [ ] Future: delete associated secrets/configmaps

---

### Task 2.7: Implement list_sessions()

**File:** `src/paude/backends/openshift.py`

**Description:** List all paude sessions in namespace.

**Acceptance Criteria:**
- [ ] Run `oc get pods -l app=paude -o json`
- [ ] Parse JSON response
- [ ] Return list of Session objects
- [ ] Include pod status mapping (Pending, Running, etc.)

---

### Task 2.8: Add OpenShift Integration Tests

**File:** `tests/test_openshift_backend.py`

**Description:** Integration tests for OpenShift backend.

**Acceptance Criteria:**
- [ ] Test pod spec generation
- [ ] Test session lifecycle (mock oc commands)
- [ ] Skip tests if no cluster available
- [ ] Marker for integration tests: `@pytest.mark.integration`

---

## Phase 3: Session Persistence

### Task 3.1: Add tmux to Container Image

**File:** `containers/paude/Dockerfile`

**Description:** Install tmux in the paude container.

**Acceptance Criteria:**
- [ ] Add tmux to package installation
- [ ] Verify tmux is available in container
- [ ] Test tmux session creation inside container

---

### Task 3.2: Create tmux-aware Entrypoint

**File:** `containers/paude/entrypoint-tmux.sh`

**Description:** Entrypoint script that manages tmux sessions.

**Acceptance Criteria:**
- [ ] Check if tmux session "claude" exists
- [ ] If exists, attach to it
- [ ] If not, create new session with claude command
- [ ] Handle arguments passed to claude
- [ ] Set proper TERM environment

**Script:**
```bash
#!/bin/bash
set -e

SESSION_NAME="claude"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Attaching to existing Claude session..."
    exec tmux attach -t "$SESSION_NAME"
else
    echo "Starting new Claude session..."
    exec tmux new-session -s "$SESSION_NAME" "claude $*"
fi
```

---

### Task 3.3: Update attach_session() for tmux

**File:** `src/paude/backends/openshift.py`

**Description:** Use tmux attach when connecting to existing session.

**Acceptance Criteria:**
- [ ] Update `attach_session()` to run entrypoint-tmux.sh
- [ ] Or directly: `oc exec -it <pod> -- tmux attach -t claude`
- [ ] Handle case where tmux session doesn't exist yet
- [ ] Return exit code from tmux

---

### Task 3.4: Add CLI Commands for Session Management

**File:** `src/paude/cli.py`

**Description:** Add attach, sessions, and stop commands.

**Acceptance Criteria:**
- [ ] `paude attach [SESSION_ID]` - attach to existing session
- [ ] `paude sessions` - list active sessions (table format)
- [ ] `paude stop [SESSION_ID]` - stop a session
- [ ] Default session ID to most recent if not specified
- [ ] Works with both backends (Podman shows message that sessions are ephemeral)

**Example Output:**
```
$ paude sessions
ID              STATUS    CREATED         WORKSPACE
abc123          running   5 minutes ago   /home/user/project
def456          running   1 hour ago      /home/user/other

$ paude attach abc123
Attaching to session abc123...
```

---

## Phase 4: Credential Management

### Task 4.1: Create Credential Secrets

**File:** `src/paude/backends/openshift.py`

**Description:** Create Kubernetes secrets from local credentials.

**Acceptance Criteria:**
- [ ] `_create_gcloud_secret()` - from ~/.config/gcloud
- [ ] `_create_gitconfig_configmap()` - from ~/.gitconfig
- [ ] `_create_claude_secret()` - from ~/.claude (if exists)
- [ ] Use session-specific names to avoid conflicts
- [ ] Handle missing credential files gracefully
- [ ] Labels for cleanup: `session-id=<id>`

---

### Task 4.2: Mount Credentials in Pod

**File:** `src/paude/backends/openshift.py`

**Description:** Update pod spec to mount credential secrets.

**Acceptance Criteria:**
- [ ] Mount gcloud secret at `/home/paude/.config/gcloud`
- [ ] Mount gitconfig at `/home/paude/.gitconfig`
- [ ] Mount claude config at `/home/paude/.claude`
- [ ] All mounts read-only
- [ ] Skip mounts for missing credentials

---

### Task 4.3: Add Setup Command

**File:** `src/paude/cli.py`

**Description:** Add setup command for initial OpenShift configuration.

**Acceptance Criteria:**
- [ ] `paude setup --backend=openshift` command
- [ ] Creates namespace if needed
- [ ] Creates persistent secrets (not session-specific)
- [ ] Verifies cluster connectivity
- [ ] Helpful output messages

---

### Task 4.4: Cleanup Credentials on Stop

**File:** `src/paude/backends/openshift.py`

**Description:** Delete session-specific secrets when session stops.

**Acceptance Criteria:**
- [ ] `stop_session()` deletes associated secrets
- [ ] `stop_session()` deletes associated configmaps
- [ ] Use label selector: `session-id=<id>`

---

## Phase 5: Automatic Image Push

### Task 5.1: Implement Registry Detection

**File:** `src/paude/backends/openshift.py`

**Description:** Detect OpenShift internal registry URL.

**Acceptance Criteria:**
- [ ] Check for default-route in openshift-image-registry
- [ ] Fall back to service URL if route not exposed
- [ ] Cache registry URL for session
- [ ] Handle registry not available error

---

### Task 5.2: Implement Registry Login

**File:** `src/paude/backends/openshift.py`

**Description:** Login to OpenShift registry using oc token.

**Acceptance Criteria:**
- [ ] Get token with `oc whoami -t`
- [ ] Run `podman login --tls-verify=false -u unused -p <token> <registry>`
- [ ] Handle login failures
- [ ] Store login state to avoid repeated logins

---

### Task 5.3: Implement Image Push

**File:** `src/paude/backends/openshift.py`

**Description:** Push local image to OpenShift registry.

**Acceptance Criteria:**
- [ ] `_ensure_image(local_tag)` method
- [ ] Tag image for registry: `<registry>/<namespace>/paude:<tag>`
- [ ] Push image with `podman push --tls-verify=false`
- [ ] Cache by image hash (don't push unchanged images)
- [ ] Show progress during push
- [ ] Handle push failures

---

### Task 5.4: Integrate with Session Start

**File:** `src/paude/backends/openshift.py`

**Description:** Automatically push image when starting session.

**Acceptance Criteria:**
- [ ] In `start_session()`, call `_ensure_image()`
- [ ] Use pushed image reference in pod spec
- [ ] Skip push if image already in registry (cache hit)
- [ ] Support custom images from devcontainer.json

---

## Phase 6: File Synchronization

### Task 6.1: Create Mutagen Wrapper

**File:** `src/paude/sync/mutagen.py`

**Description:** Wrapper for mutagen CLI operations.

**Acceptance Criteria:**
- [ ] `MutagenSync` class with methods:
  - `start(local_path, remote_spec, name)` - create sync session
  - `stop(name)` - terminate session
  - `status(name)` - get sync status
  - `flush(name)` - force immediate sync
  - `list()` - list all sessions
- [ ] Handle mutagen not installed error
- [ ] Parse mutagen JSON output

---

### Task 6.2: Configure Mutagen for Kubernetes

**File:** `src/paude/sync/mutagen.py`

**Description:** Configure mutagen to sync with Kubernetes pod.

**Acceptance Criteria:**
- [ ] Build remote spec: `kubernetes://pod-name/path`
- [ ] Configure two-way-resolved mode (local wins)
- [ ] Set ignore patterns: `.git/objects`, `__pycache__`, `.venv`, `node_modules`
- [ ] Configure labels for session management
- [ ] Handle connection failures

**Mutagen Command:**
```bash
mutagen sync create \
  --name=paude-abc123 \
  --sync-mode=two-way-resolved \
  --ignore=".git/objects" \
  --ignore="__pycache__" \
  --ignore=".venv" \
  --ignore="node_modules" \
  /local/path \
  kubernetes://pod-name:/workspace
```

---

### Task 6.3: Integrate Sync with Session Lifecycle

**File:** `src/paude/backends/openshift.py`

**Description:** Start/stop mutagen sync with session.

**Acceptance Criteria:**
- [ ] Start mutagen sync after pod is ready
- [ ] Wait for initial sync before attaching
- [ ] Stop mutagen sync when session stops
- [ ] Handle sync errors gracefully

---

### Task 6.4: Add Workspace PVC

**File:** `src/paude/backends/openshift.py`

**Description:** Create PVC for persistent workspace storage.

**Acceptance Criteria:**
- [ ] `_create_workspace_pvc(session_id)` method
- [ ] PVC spec: 10Gi, RWO, default storage class
- [ ] Mount PVC at /workspace in pod
- [ ] Delete PVC on session stop (optional, configurable)

---

### Task 6.5: Add Sync CLI Commands

**File:** `src/paude/cli.py`

**Description:** Add sync-related commands.

**Acceptance Criteria:**
- [ ] `paude sync --status` - show sync status
- [ ] `paude sync --flush` - force immediate sync
- [ ] Error if no active session

---

## Phase 7: Network Filtering

**Important:** This phase implements a **two-pod design** (not sidecar) to achieve network isolation equivalent to Podman's internal network. Kubernetes NetworkPolicy operates at the pod level, not container level, so a sidecar cannot have different network access than the main container.

### Task 7.1: Create Squid Proxy Deployment

**File:** `src/paude/backends/openshift.py`

**Description:** Deploy squid proxy as a separate Deployment with a Service.

**Acceptance Criteria:**
- [ ] Create `_generate_squid_deployment_spec()` method
- [ ] Deployment spec includes:
  - Name: `squid-proxy`
  - Labels: `app=squid-proxy`
  - Replicas: 1
  - Container using same squid image as local
  - Port 3128 exposed
  - Resource requests/limits
- [ ] Create `_generate_squid_service_spec()` method
- [ ] Service spec includes:
  - Name: `squid-proxy`
  - Selector: `app=squid-proxy`
  - Port: 3128 → 3128

**Deployment Spec Example:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: squid-proxy
  labels:
    app: squid-proxy
spec:
  replicas: 1
  selector:
    matchLabels:
      app: squid-proxy
  template:
    metadata:
      labels:
        app: squid-proxy
    spec:
      containers:
        - name: squid
          image: <registry>/paude/paude-proxy:latest
          ports:
            - containerPort: 3128
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "500m"
```

---

### Task 7.2: Create NetworkPolicy for Egress Restriction

**File:** `src/paude/backends/openshift.py`

**Description:** Create NetworkPolicies to restrict paude pod egress to only DNS and squid-proxy.

**Acceptance Criteria:**
- [ ] Create `_generate_egress_deny_policy()` method
- [ ] Create `_generate_egress_allow_policy()` method
- [ ] Deny policy blocks all egress for pods with `app=paude, role=workload`
- [ ] Allow policy permits:
  - DNS (UDP/TCP 53) to openshift-dns and kube-system namespaces
  - TCP 3128 to pods with `app=squid-proxy` label
- [ ] Policies are idempotent (create or update)

**NetworkPolicy Spec Example:**
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: paude-allow-dns-and-proxy
spec:
  podSelector:
    matchLabels:
      app: paude
      role: workload
  policyTypes:
    - Egress
  egress:
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
    - to:
        - podSelector:
            matchLabels:
              app: squid-proxy
      ports:
        - protocol: TCP
          port: 3128
```

---

### Task 7.3: Push Proxy Image

**File:** `src/paude/backends/openshift.py`

**Description:** Push proxy image to OpenShift registry.

**Acceptance Criteria:**
- [ ] `_ensure_proxy_image()` method
- [ ] Push paude-proxy image alongside main image
- [ ] Handle push failures
- [ ] Cache by image hash (don't push unchanged images)

---

### Task 7.4: Configure Proxy Environment in Paude Pod

**File:** `src/paude/backends/openshift.py`

**Description:** Set proxy environment variables in paude pod spec.

**Acceptance Criteria:**
- [ ] Add labels to paude pod: `app=paude, role=workload`
- [ ] Set HTTP_PROXY, HTTPS_PROXY pointing to `http://squid-proxy:3128`
- [ ] Set NO_PROXY for cluster-internal addresses if needed
- [ ] Respect `--allow-network` flag:
  - If set, skip NetworkPolicy creation
  - If set, don't set HTTP_PROXY/HTTPS_PROXY
  - Display warning about full network access

---

### Task 7.5: Ensure Squid Proxy Running on Session Start

**File:** `src/paude/backends/openshift.py`

**Description:** Ensure squid proxy Deployment and Service exist before creating paude session.

**Acceptance Criteria:**
- [ ] In `start_session()`, call `_ensure_squid_proxy()` if `network_restricted=True`
- [ ] `_ensure_squid_proxy()` creates or updates Deployment and Service
- [ ] Wait for squid-proxy pod to be ready
- [ ] Skip if `network_restricted=False` (--allow-network)

---

### Task 7.6: Create NetworkPolicies on Namespace Setup

**File:** `src/paude/backends/openshift.py`

**Description:** Create NetworkPolicies during namespace setup.

**Acceptance Criteria:**
- [ ] In `_ensure_namespace()`, create NetworkPolicies
- [ ] Policies are namespace-scoped
- [ ] Policies apply to all future paude session pods
- [ ] Idempotent: don't fail if policies already exist

---

### Task 7.7: Add EgressFirewall (Optional Defense-in-Depth)

**File:** `src/paude/backends/openshift.py`

**Description:** Create OVN-Kubernetes EgressFirewall as defense-in-depth.

**Acceptance Criteria:**
- [ ] Check if OVN-Kubernetes is cluster CNI (detect API availability)
- [ ] Create EgressFirewall allowing *.googleapis.com, *.google.com
- [ ] Skip if OVN-Kubernetes not available (not all clusters support it)
- [ ] Don't fail session start if EgressFirewall creation fails
- [ ] Log warning if EgressFirewall not available

**EgressFirewall Spec Example:**
```yaml
apiVersion: k8s.ovn.org/v1
kind: EgressFirewall
metadata:
  name: paude-egress-firewall
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

---

### Task 7.8: Add Network Filtering Integration Tests

**File:** `tests/test_openshift_network.py`

**Description:** Integration tests for network filtering.

**Acceptance Criteria:**
- [ ] Test NetworkPolicy spec generation
- [ ] Test squid Deployment/Service spec generation
- [ ] Test that paude pod spec includes correct labels
- [ ] Test that paude pod spec includes HTTP_PROXY env vars
- [ ] Test `--allow-network` flag skips network restrictions
- [ ] Skip tests if no cluster available
- [ ] Marker for integration tests: `@pytest.mark.integration`

---

### Task 7.9: Verify Network Isolation E2E

**File:** `tests/test_openshift_e2e.py`

**Description:** End-to-end test verifying network isolation works.

**Acceptance Criteria:**
- [ ] Start session with network filtering enabled
- [ ] Verify paude pod cannot reach external URLs directly
- [ ] Verify paude pod can reach googleapis.com via proxy
- [ ] Verify blocked domains are rejected by squid
- [ ] Start session with `--allow-network` flag
- [ ] Verify paude pod can reach external URLs directly
- [ ] Skip tests if no cluster available

---

## Phase 8: Polish & Documentation

### Task 8.1: Error Handling Improvements

**File:** `src/paude/backends/openshift.py`

**Description:** Improve error messages and recovery.

**Acceptance Criteria:**
- [ ] Clear error for "oc not installed"
- [ ] Clear error for "not logged in"
- [ ] Clear error for "namespace doesn't exist"
- [ ] Clear error for "image push failed"
- [ ] Clear error for "pod failed to start"
- [ ] Clear error for "mutagen not installed"
- [ ] Cleanup partial resources on failure

---

### Task 8.2: Add Progress Indicators

**File:** `src/paude/backends/openshift.py`

**Description:** Show progress during long operations.

**Acceptance Criteria:**
- [ ] Progress for image push
- [ ] Progress for pod creation/waiting
- [ ] Progress for initial sync
- [ ] Use rich or simple stderr messages

---

### Task 8.3: Write User Documentation

**File:** `docs/OPENSHIFT.md`

**Description:** Comprehensive user guide for OpenShift backend.

**Acceptance Criteria:**
- [ ] Prerequisites (oc CLI, mutagen, cluster access)
- [ ] One-time setup instructions
- [ ] Basic usage examples
- [ ] Configuration options
- [ ] Troubleshooting section
- [ ] Security considerations

---

### Task 8.4: Update README

**File:** `README.md`

**Description:** Add OpenShift backend to main README.

**Acceptance Criteria:**
- [ ] Brief mention of OpenShift support
- [ ] Link to detailed docs
- [ ] Example commands

---

### Task 8.5: Add Resource Configuration

**File:** `src/paude/backends/openshift.py`

**Description:** Support configurable resource limits.

**Acceptance Criteria:**
- [ ] Read from paude.json backend.resources
- [ ] Default: 500m CPU, 1Gi memory request
- [ ] Default: 2 CPU, 4Gi memory limit
- [ ] Validate resource specifications

---

### Task 8.6: Session Timeout/Cleanup

**File:** `src/paude/backends/openshift.py`

**Description:** Implement session timeouts and cleanup.

**Acceptance Criteria:**
- [ ] Read timeout from config (default: 4h idle, 24h max)
- [ ] `paude cleanup` command to remove stale sessions
- [ ] Option to keep sessions running indefinitely

---

## Testing Checklist

After all phases:

- [ ] Fresh install works on macOS
- [ ] Fresh install works on Linux
- [ ] Works with OpenShift Local (CRC)
- [ ] Works with remote OpenShift cluster
- [ ] Works with ROSA/ARO/OpenShift Dedicated
- [ ] Session survives network drop
- [ ] Session survives laptop sleep
- [ ] File sync handles large files
- [ ] File sync handles many small files
- [ ] Git operations work (commit, status, diff)
- [ ] Vertex AI authentication works
- [ ] **Network isolation: paude pod cannot reach internet directly (curl fails)**
- [ ] **Network isolation: paude pod can reach googleapis.com via squid proxy**
- [ ] **Network isolation: squid proxy blocks non-allowed domains**
- [ ] **NetworkPolicy: verified paude pod egress limited to DNS + squid-proxy only**
- [ ] `--allow-network` enables full access (no NetworkPolicy, no proxy)
- [ ] Multiple sessions work simultaneously (share squid-proxy)
- [ ] Cleanup removes session resources (but leaves shared squid-proxy)
- [ ] `paude stop --all` removes all resources including squid-proxy
