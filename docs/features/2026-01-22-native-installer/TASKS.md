# Native Installer Migration - Implementation Tasks

## Phase 1: Dockerfile Migration

### Task 1.1: Update base image

**File**: `containers/paude/Dockerfile`

**Current**:
```dockerfile
FROM node:22-slim
```

**Change to**:
```dockerfile
FROM debian:bookworm-slim
```

**Acceptance criteria**:
- [ ] Image builds successfully
- [ ] Base image is Debian bookworm-slim

---

### Task 1.2: Update system dependencies

**File**: `containers/paude/Dockerfile`

**Current**:
```dockerfile
RUN apt-get update && \
    apt-get install -y git curl wget dnsutils iputils-ping jq perl make && \
    rm -rf /var/lib/apt/lists/*
```

**Change to**:
```dockerfile
RUN apt-get update && \
    apt-get install -y \
        git \
        curl \
        wget \
        dnsutils \
        iputils-ping \
        jq \
        perl \
        make \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*
```

**Notes**:
- Added `ca-certificates` for HTTPS downloads
- Added `bash` explicitly (may not be in slim image)
- Removed Node.js (no longer needed)
- Removed ripgrep (bundled with native installer)

**Acceptance criteria**:
- [ ] All required utilities available in container
- [ ] HTTPS connections work (ca-certificates)
- [ ] bash is available for installer script

---

### Task 1.3: Create user before installation

**File**: `containers/paude/Dockerfile`

**Current order**:
1. npm install (as root)
2. useradd paude

**New order**:
1. useradd paude
2. Install Claude Code as paude user

**Change to**:
```dockerfile
# Create user first (installer runs as this user)
RUN useradd -m -s /bin/bash paude
```

**Acceptance criteria**:
- [ ] User created before Claude Code installation
- [ ] User has home directory at /home/paude

---

### Task 1.4: Replace npm install with native installer

**File**: `containers/paude/Dockerfile`

**Current**:
```dockerfile
RUN npm install -g @anthropic-ai/claude-code
```

**Change to**:
```dockerfile
# Install Claude Code using native installer
USER paude
WORKDIR /home/paude
RUN curl -fsSL https://claude.ai/install.sh | bash
```

**Acceptance criteria**:
- [ ] Installer runs successfully as paude user
- [ ] Claude binary installed to /home/paude/.local/bin/claude
- [ ] No errors during installation

---

### Task 1.5: Update PATH environment

**File**: `containers/paude/Dockerfile`

**Add**:
```dockerfile
# Ensure claude binary is in PATH
ENV PATH="/home/paude/.local/bin:$PATH"
```

**Acceptance criteria**:
- [ ] `claude` command works without full path
- [ ] PATH persists for container runtime

---

### Task 1.6: Disable auto-updates

**File**: `containers/paude/Dockerfile`

**Add**:
```dockerfile
# Disable auto-updates (container is ephemeral, version controlled by image)
ENV DISABLE_AUTOUPDATER=1
```

**Acceptance criteria**:
- [ ] No auto-update checks on container startup
- [ ] Faster container startup time

---

### Task 1.7: Update ENTRYPOINT handling

**File**: `containers/paude/Dockerfile`

**Current**:
```dockerfile
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**Change to** (note: USER already set in 1.4):
```dockerfile
# Switch back to root to copy entrypoint, then back to paude
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**Acceptance criteria**:
- [ ] entrypoint.sh is executable
- [ ] Container runs as paude user
- [ ] Working directory is set appropriately

---

### Task 1.8: Complete Dockerfile

**File**: `containers/paude/Dockerfile`

**Full new Dockerfile**:
```dockerfile
FROM debian:bookworm-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y \
        git \
        curl \
        wget \
        dnsutils \
        iputils-ping \
        jq \
        perl \
        make \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash paude

# Install Claude Code using native installer (as paude user)
USER paude
WORKDIR /home/paude
RUN curl -fsSL https://claude.ai/install.sh | bash

# Disable auto-updates (version controlled by image rebuild)
ENV DISABLE_AUTOUPDATER=1

# Ensure claude is in PATH
ENV PATH="/home/paude/.local/bin:$PATH"

# Copy entrypoint (requires root, then switch back)
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Run as non-root user
USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

**Acceptance criteria**:
- [ ] Full Dockerfile builds without errors
- [ ] All previous task criteria met

---

## Phase 2: Testing and Validation

### Task 2.1: Build image

**Command**:
```bash
cd containers/paude
podman build -t paude:native-test .
```

**Acceptance criteria**:
- [ ] Image builds successfully
- [ ] No errors or warnings during build

---

### Task 2.2: Verify claude binary

**Command**:
```bash
podman run --rm paude:native-test claude --version
```

**Acceptance criteria**:
- [ ] Version string printed
- [ ] No errors about missing binary or PATH

---

### Task 2.3: Verify ripgrep bundled

**Command**:
```bash
podman run --rm paude:native-test rg --version
```

**Acceptance criteria**:
- [ ] ripgrep version printed
- [ ] Bundled with native installer (no separate apt install)

---

### Task 2.4: Verify auto-update disabled

**Command**:
```bash
podman run --rm -e DISABLE_AUTOUPDATER=1 paude:native-test claude --help
```

**Acceptance criteria**:
- [ ] No update check messages
- [ ] Fast startup

---

### Task 2.5: Compare image sizes

**Commands**:
```bash
# Old image
podman images paude:npm --format "{{.Size}}"

# New image
podman images paude:native-test --format "{{.Size}}"
```

**Acceptance criteria**:
- [ ] New image is same size or smaller
- [ ] Document size comparison in PR

---

### Task 2.6: Run existing test suite

**Command**:
```bash
make test
```

**Acceptance criteria**:
- [ ] All Python tests pass
- [ ] All bash tests pass

---

### Task 2.7: End-to-end manual test

**Procedure**:
1. Run `PAUDE_DEV=1 ./paude --version`
2. Run `PAUDE_DEV=1 ./paude` and enter a simple prompt
3. Verify Claude responds
4. Verify network filtering works (try blocked domain)
5. Exit and verify clean shutdown

**Acceptance criteria**:
- [ ] All manual tests pass
- [ ] Behavior identical to npm-based image

---

## Phase 3: Documentation Updates

### Task 3.1: Update README.md

**File**: `README.md`

**Changes**:
- Update any references to npm installation
- Note that container uses native installer
- Document image rebuild for updates

**Acceptance criteria**:
- [ ] No outdated npm references
- [ ] Clear documentation on how to update Claude version

---

### Task 3.2: Update container README if exists

**File**: `containers/paude/README.md` (if exists)

**Changes**:
- Document Dockerfile changes
- Note native installer usage

**Acceptance criteria**:
- [ ] Container-specific docs updated

---

## Summary Checklist

### Phase 1: Dockerfile Migration
- [ ] Update base image to debian:bookworm-slim
- [ ] Update system dependencies
- [ ] Create user before installation
- [ ] Replace npm install with native installer
- [ ] Update PATH environment
- [ ] Disable auto-updates
- [ ] Fix ENTRYPOINT handling
- [ ] Complete Dockerfile assembled

### Phase 2: Testing and Validation
- [ ] Image builds successfully
- [ ] claude --version works
- [ ] ripgrep works (bundled)
- [ ] Auto-update disabled verified
- [ ] Image size compared
- [ ] Test suite passes
- [ ] Manual e2e test passes

### Phase 3: Documentation Updates
- [ ] README.md updated
- [ ] Container docs updated (if applicable)

---

## Effort Estimate

**T-shirt size**: **Small (S)**

**Breakdown**:
- Dockerfile changes: 1-2 hours
- Testing and validation: 2-3 hours
- Documentation: 1 hour
- PR and review: 1-2 hours

**Total**: ~1 day of focused work

**Risk factors**:
- Low: Straightforward migration path
- Installer script behavior may differ from expectations (verify during testing)
- Potential need for troubleshooting if installer fails in container context
