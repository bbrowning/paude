# Paude Security Hardening Tasks

This file tracks security improvements needed before running Claude Code autonomously inside the paude container. Work through tasks in order of priority.

## How to Use This File

1. Work on one task at a time, starting with CRITICAL priority
2. For each task:
   - Read the threat description and impact
   - Review the proposed mitigation options
   - Implement and test the solution
   - Update task status to `COMPLETED` with implementation notes
   - Commit changes before moving to next task
3. After completing all CRITICAL tasks, evaluate if you're comfortable with semi-autonomous execution
4. MEDIUM tasks can be done as defense-in-depth improvements

## Progress Summary

- **CRITICAL**: 6 completed, 0 remaining ✅
- **HIGH**: 0 completed, 4 remaining
- **MEDIUM**: 0 completed, 2 remaining

---

## CRITICAL Priority Tasks

### ✅ TASK 1: SSH-Based Git Push Prevention [COMPLETED]

**Status**: COMPLETED
**Completed**: Initial implementation
**Threat**: Unauthorized code pushed to remote repositories via SSH
**Impact**: Supply chain attacks, malicious code in production

**Mitigation Implemented**:
- No `~/.ssh` directory mounted in container
- SSH keys completely inaccessible to Claude Code

**Verification**:
```bash
./paude
# Inside container:
git remote -v
git push  # Should fail with no SSH keys available
```

---

### ✅ TASK 2: GitHub CLI Operations Prevention [COMPLETED]

**Status**: COMPLETED
**Completed**: Initial implementation
**Threat**: Unauthorized GitHub operations (PR creation, issue manipulation, releases)
**Impact**: Repository tampering, unauthorized releases

**Mitigation Implemented**:
- No `~/.config/gh` directory mounted
- GitHub CLI cannot authenticate

**Verification**:
```bash
./paude
# Inside container:
gh auth status  # Should show not authenticated
```

---

### ✅ TASK 3: Cloud Credential Protection [COMPLETED]

**Status**: COMPLETED
**Completed**: Initial implementation
**Threat**: Modification of cloud authentication credentials
**Impact**: Persistent access, credential tampering

**Mitigation Implemented**:
- `~/.config/gcloud` mounted read-only (`:ro`)
- Git config mounted read-only (`:ro`)

**Verification**:
```bash
./paude
# Inside container:
touch ~/.config/gcloud/test.txt  # Should fail (read-only)
```

---

### ✅ TASK 4: Network Exfiltration Prevention [COMPLETED]

**Status**: COMPLETED
**Completed**: Proxy sidecar with domain allowlist
**Threat**: Claude can send any file to attacker-controlled servers via curl/wget/WebFetch
**Impact**: COMPLETE DATA BREACH - all workspace files, secrets, source code stolen

**Mitigation Implemented**:
- Proxy sidecar architecture with domain-based allowlist
- Claude container on internal network (no direct internet access)
- Proxy container bridges internal and external networks
- Only Google/Vertex AI domains permitted through proxy
- Opt-in: `--allow-network` flag bypasses proxy for full access

**Architecture**:
```
┌─────────────────────────────────────────────────────┐
│  paude-internal (--internal, no internet)           │
│  ┌───────────┐      ┌─────────────────────────────┐ │
│  │  Claude   │─────▶│  Proxy (squid allowlist)    │─┼──▶ *.googleapis.com
│  │ Container │      │  paude-internal + podman    │ │    *.google.com
│  └───────────┘      └─────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Allowed Domains** (in proxy/squid.conf):
- `*.googleapis.com` - Vertex AI API
- `*.google.com` - Google auth
- `accounts.google.com` - OAuth
- `oauth2.googleapis.com` - Token refresh
- `*.gstatic.com` - Google static assets

**Workflow Pattern**:
- **Execution mode** (default): `./paude` - proxy-filtered network, only Vertex AI
- **Research mode**: `./paude --allow-network` - full network, treat outputs carefully

**Files Added**:
- `proxy/Dockerfile` - Proxy container image
- `proxy/squid.conf` - Allowlist configuration

**Verification**:
```bash
# Test default (proxy-filtered):
./paude
# Inside container:
curl https://evil.com  # Should fail - blocked by proxy
curl https://example.com  # Should fail - not in allowlist
# Vertex AI calls work via gcloud/Claude Code

# Test with network enabled:
./paude --allow-network
# Should print warning, full network access
curl https://example.com  # Should succeed
```

**Implementation Notes**:
- Hardcoded DNS (8.8.8.8, 8.8.4.4) required because `--internal` network has no DNS
- Network order `$INTERNAL_NETWORK,podman` works; reverse order breaks DNS
- `podman kill` used for instant cleanup (no graceful shutdown delay)
- Script refactored into functions for maintainability

**Acceptance Criteria**:
- [x] Claude Code can only reach Google/Vertex AI domains by default
- [x] Arbitrary HTTP/HTTPS requests blocked by proxy
- [x] Explicit opt-in via --allow-network flag
- [x] Proxy lifecycle tied to Claude container (cleanup on exit)
- [x] Concurrent sessions supported (unique proxy per session)
- [x] Documentation updated with architecture

---

### ✅ TASK 5: Workspace Filesystem Protection [COMPLETED]

**Status**: COMPLETED
**Completed**: Mitigated via documentation
**Threat**: Claude can delete/modify all project files including .git directory
**Impact**: COMPLETE PROJECT DESTRUCTION - rm -rf on all files, corrupted git history

**Mitigation Implemented**: Documentation-based mitigation

Technical solutions (overlay mounts, snapshots, bind mount tricks) were evaluated and rejected:
- Overlay mounts desync container from host, requiring manual merge steps
- Volume copies have same desync problem
- Bind mount on .git prevents directory deletion but not content deletion (useless)
- Read-only .git prevents commits (breaks normal workflow)

**Resolution**: Git's distributed nature is the protection mechanism. Every push to a remote is a complete backup. This matches how git is designed and how developers already think about safety.

**Documentation Added** (README.md - "Workspace Protection" section):
- Container has full read-write access to workspace
- Protection = push important work to a remote before autonomous sessions
- Remote can be GitHub, GitLab, or a local bare repo
- Recovery = clone from remote

**Pre-flight Check Added** (paude script):
- Warns if workspace has no git repository
- Warns if git repository has no remotes configured
- Non-blocking: shows warning and continues

**Residual Risk**: Uncommitted and unpushed work is at risk. This is acceptable because:
- Same risk exists with laptop failure, disk corruption, etc.
- Users already understand "push = safe"
- No complexity added to paude

**Acceptance Criteria**:
- [x] Recovery process documented (push to remote, clone to recover)
- [x] User responsibility clearly stated
- [x] No complex infrastructure required
- [x] Pre-flight warning for missing git repo or remotes

---

### ✅ TASK 6: HTTPS Git Push Prevention [COMPLETED]

**Status**: COMPLETED
**Completed**: Risk accepted - credentials not stored in URLs
**Threat**: Git can still push via HTTPS if credentials cached or in .git/config
**Impact**: Unauthorized code pushed to remote repositories

**Mitigation Implemented**: Risk acceptance

HTTPS git push requires credentials to be available. The attack vectors are:
1. Credentials embedded in remote URL (e.g., `https://user:token@github.com/...`)
2. Credential helper caching tokens
3. Interactive password prompt

In the paude container context:
- No credential helpers are mounted (no `~/.git-credentials`, no OS keychain access)
- Interactive prompts would require human intervention
- Embedding credentials in `.git/config` URLs is a bad practice that users are responsible for avoiding

**Residual Risk**: If a user embeds credentials directly in their `.git/config` remote URLs, push would work. This is:
- Considered a user misconfiguration, not a container security issue
- Already a security anti-pattern (credentials in plaintext files)
- The user's responsibility to avoid

**Why Not Implemented**:
- Git hooks to block push would also block legitimate local workflows
- Network filtering (blocking github.com) would break clone/fetch operations
- Disabling credential helpers system-wide has no effect if credentials are in URLs

**Acceptance Criteria**:
- [x] SSH git push fails (verified in Task 1)
- [x] HTTPS git push fails without credentials (no helpers mounted)
- [x] Risk documented for credential-in-URL edge case
- [x] User responsibility for not embedding credentials in URLs

---

## HIGH Priority Tasks

### ⚠️ TASK 7: Plugin System Audit and Lockdown [PENDING]

**Status**: PENDING
**Threat**: Malicious plugins in ~/.claude can execute arbitrary code with full access
**Impact**: Persistent backdoor, execution outside container context

**Current State**: `~/.claude` mounted read-write at TWO locations (container path + host path)

**Investigation Phase**:
```bash
# 1. List all plugins
ls -la ~/.claude/
find ~/.claude -type f -name "*.js" -o -name "*.ts"

# 2. Review each plugin for:
# - Network access (fetch, http, https modules)
# - File system access (fs module)
# - Process execution (child_process, exec)
# - Eval or dynamic code execution

# 3. Check Claude Code plugin documentation
# What permissions do plugins have?
```

**Mitigation Options**:

**Option A: Remove Dual Mount** (Simplest)
```bash
# In paude script, remove line 54:
# MOUNT_ARGS+=(-v "$CLAUDE_DIR:$CLAUDE_DIR:rw")

# Keep only: -v "$CLAUDE_DIR:/home/paude/.claude:rw"
# Cons: May break plugins with hardcoded paths
```

**Option B: Read-Only Plugin Mount** (Recommended)
```bash
# Change both mounts to read-only
MOUNT_ARGS+=(-v "$CLAUDE_DIR:/home/paude/.claude:ro")
MOUNT_ARGS+=(-v "$CLAUDE_DIR:$CLAUDE_DIR:ro")

# Pros: Plugins can't modify themselves or add new plugins
# Cons: Plugins can't store state/cache
```

**Option C: Plugin Sandboxing** (Advanced)
```bash
# Research: Does Claude Code support plugin sandboxing?
# Check if plugins can be run in restricted mode
# May need to wait for upstream support
```

**Testing Plan**:
```bash
# After implementing mitigation:
./paude
# Verify plugins still load and function
# Verify plugins cannot write to ~/.claude
touch ~/.claude/test.txt  # Should fail if read-only
```

**Acceptance Criteria**:
- [ ] All plugins audited for malicious code
- [ ] Plugins cannot modify ~/.claude directory
- [ ] Plugins cannot write to host filesystem
- [ ] Document trusted plugins list
- [ ] Process to audit new plugins before adding

---

### ⚠️ TASK 8: Command Audit Logging [PENDING]

**Status**: PENDING
**Threat**: No visibility into bash commands Claude executes
**Impact**: No forensics after incident, can't review what happened

**Current State**: No logging of bash commands

**Mitigation Implementation**:

**Option A: Bash History to Mounted File** (Simplest)
```bash
# In paude script, add mount:
MOUNT_ARGS+=(-v "/tmp/paude-audit-$(date +%s).log:/home/paude/.bash_history:rw")

# In Dockerfile, configure bash history:
RUN echo 'export HISTFILE=/home/paude/.bash_history' >> /home/paude/.bashrc && \
    echo 'export HISTTIMEFORMAT="%F %T "' >> /home/paude/.bashrc && \
    echo 'export HISTSIZE=10000' >> /home/paude/.bashrc && \
    echo 'shopt -s histappend' >> /home/paude/.bashrc && \
    echo 'PROMPT_COMMAND="history -a"' >> /home/paude/.bashrc
```

**Option B: Structured Audit Log** (Better)
```bash
# Create audit logging script that captures:
# - Timestamp
# - Command
# - Exit code
# - Working directory

# Add to Dockerfile:
COPY audit-wrapper.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/audit-wrapper.sh

# Wrapper bash to log all commands
```

**Option C: Full Session Recording** (Most Complete)
```bash
# Use 'script' command to record entire session
# In paude, wrap execution:
podman run ... script -f /tmp/session.log -c "claude $@"
```

**Testing Plan**:
```bash
./paude
# Run various commands
ls -la
cat /etc/passwd
git status

# Exit and verify audit log
cat /tmp/paude-audit-*.log
# Should show all commands with timestamps
```

**Acceptance Criteria**:
- [ ] All bash commands logged with timestamps
- [ ] Logs persisted to host filesystem
- [ ] Logs include exit codes
- [ ] Log format is parsable (for analysis)
- [ ] Logs rotated/managed to prevent disk fill

---

### ⚠️ TASK 9: Secrets and Environment Variable Protection [PENDING]

**Status**: PENDING
**Threat**: .env files, API keys, tokens readable and exfiltrable
**Impact**: Credential theft, API abuse, account compromise

**Current State**: All workspace files accessible, including secrets

**Investigation Phase**:
```bash
# 1. Identify what secrets exist in typical projects
find . -name ".env*" -o -name "*secret*" -o -name "*credentials*"

# 2. Document what secrets Claude Code legitimately needs
# Does it need API keys? Database credentials? Which ones?
```

**Mitigation Options**:

**Option A: Exclude Secrets from Mount** (Requires selective mounting)
```bash
# In paude script, don't mount common secret paths
# Problem: Complex, need to mount everything EXCEPT secrets
# May need to use tmpfs overlay
```

**Option B: Secrets via Environment Variables Only** (Recommended)
```bash
# 1. Never commit secrets to git
# 2. Add to .gitignore: .env*, secrets.*, credentials.*
# 3. Pass secrets via ENV_ARGS instead of files

# In paude script:
ENV_ARGS+=(-e "DATABASE_URL=${DATABASE_URL}")
ENV_ARGS+=(-e "API_KEY=${API_KEY}")

# 4. Document workflow: secrets go in host environment, not files
```

**Option C: Podman Secrets** (Most Secure)
```bash
# Use podman secrets feature
podman secret create db_password ./db_password.txt

# Mount as read-only file
podman run --secret db_password ...
# Available at /run/secrets/db_password
```

**Option D: Secrets Detection Pre-Flight** (Defense in Depth)
```bash
# Before running paude, scan workspace for secrets
# Use tool like truffleHog, gitleaks, detect-secrets

# In paude script, add:
if ! command -v gitleaks &> /dev/null; then
    echo "Warning: gitleaks not installed, cannot scan for secrets"
else
    gitleaks detect --source "$WORKSPACE_DIR" --no-git || {
        echo "Secrets detected! Resolve before running paude."
        exit 1
    }
fi
```

**Testing Plan**:
```bash
# Create test secrets
echo "API_KEY=super_secret_123" > .env
echo "password=hunter2" > secrets.txt

# Verify mitigation
./paude
# Should either:
# - Not mount these files (Option A)
# - Warn and exit (Option D)
# - Be passed as env only (Option B)
```

**Acceptance Criteria**:
- [ ] .env files cannot be read by Claude Code
- [ ] Secrets passed via podman secrets or ENV only
- [ ] Pre-flight secrets scanning implemented
- [ ] Documentation on proper secrets management
- [ ] .gitignore includes common secret file patterns

---

### ⚠️ TASK 10: Git History Protection [PENDING]

**Status**: PENDING
**Threat**: Claude can delete .git directory or corrupt history with rebases
**Impact**: Loss of version control, corrupted project history

**Current State**: .git directory fully accessible and writable

**Mitigation Options**:

**Option A: Read-Only .git Directory** (Simplest)
```bash
# In paude script, add additional mount:
MOUNT_ARGS+=(-v "$WORKSPACE_DIR/.git:$WORKSPACE_DIR/.git:ro")

# Pros: Complete protection
# Cons: Claude can't commit, create branches, etc.
# May be too restrictive for normal workflows
```

**Option B: Git Directory Backup Before Session** (Recommended)
```bash
# In paude script, before exec:
if [[ -d "$WORKSPACE_DIR/.git" ]]; then
    BACKUP_DIR="/tmp/paude-git-backup-$(date +%s)"
    cp -r "$WORKSPACE_DIR/.git" "$BACKUP_DIR"
    echo "Git directory backed up to: $BACKUP_DIR"
fi

# Add helper script to restore:
# restore-git.sh <backup-dir>
```

**Option C: Git Reflog Protection** (Defense in Depth)
```bash
# Git reflog can recover from rebases/resets
# Ensure it's enabled and configure retention

# In Dockerfile:
RUN git config --system core.logAllRefUpdates true && \
    git config --system gc.reflogExpire never && \
    git config --system gc.reflogExpireUnreachable never
```

**Option D: Git Hook to Prevent .git Deletion** (Best with Option B)
```bash
# Create inotify watch or periodic check
# In container, monitor .git directory
# If deletion detected, immediately abort

# Or simpler: Make .git immutable (requires privileged container)
chattr +i .git  # Requires --privileged
```

**Testing Plan**:
```bash
# Create test repo
git init test-repo && cd test-repo
echo "test" > file.txt
git add . && git commit -m "test"

./paude
# Try destructive operations:
rm -rf .git  # Should be prevented or recoverable
git rebase --root  # Should be recoverable via reflog

# Verify recovery
# Restore .git from backup or use reflog
git reflog
git reset --hard HEAD@{1}
```

**Acceptance Criteria**:
- [ ] .git directory backed up before each session
- [ ] Can recover from .git deletion
- [ ] Can recover from destructive rebases
- [ ] Recovery documented in README
- [ ] Reflog configured for maximum retention
- [ ] Consider: Warning when .git is about to be modified

---

## MEDIUM Priority Tasks

### ⚠️ TASK 11: Container Resource Limits [PENDING]

**Status**: PENDING
**Threat**: Resource exhaustion attacks, denial of service
**Impact**: System slowdown, host resource starvation

**Current State**: No CPU, memory, or process limits

**Mitigation Implementation**:

```bash
# In paude script, add resource limits to line 102:
exec podman run --rm -it \
    --cpus=2 \
    --memory=4g \
    --memory-swap=4g \
    --pids-limit=100 \
    --ulimit nofile=1024:1024 \
    -w "$WORKSPACE_DIR" \
    ...
```

**Tuning Guidance**:
- `--cpus`: 2-4 cores depending on host capacity
- `--memory`: 4-8GB depending on project size
- `--pids-limit`: 100-500 depending on expected processes
- `--ulimit nofile`: Limit open files to prevent file descriptor exhaustion

**Testing Plan**:
```bash
./paude
# Inside container, try resource exhaustion:
:(){ :|:& };:  # Fork bomb (should be stopped by pids-limit)
# Should hit limit and not crash host
```

**Acceptance Criteria**:
- [ ] CPU usage capped
- [ ] Memory usage capped
- [ ] Fork bomb prevented by pids-limit
- [ ] Limits don't interfere with normal development
- [ ] Limits documented and configurable

---

### ⚠️ TASK 12: Package Installation Controls [PENDING]

**Status**: PENDING
**Threat**: Installation of malicious npm/pip packages
**Impact**: Supply chain compromise, malicious code execution

**Current State**: Can install any package via npm/pip

**Mitigation Options**:

**Option A: Package Registry Mirror** (Complex but secure)
```bash
# Set up private registry mirror with approved packages
# Configure npm/pip to use only approved mirror
```

**Option B: Read-Only Container Filesystem** (Recommended)
```bash
# In paude, add:
podman run --read-only \
    --tmpfs /tmp \
    --tmpfs /home/paude/.npm \
    ...

# Pros: Can't install packages that persist
# Cons: Need tmpfs for package caches
```

**Option C: Package Audit Pre-Flight** (Defense in Depth)
```bash
# Before running npm install, audit package.json changes
# Use npm audit, snyk, or socket.dev

# Could add git hook to verify package.json changes
```

**Option D: Network Restrictions** (If Task 4 Option B implemented)
```bash
# Allowlist only:
# - registry.npmjs.org
# - pypi.org
# Block direct GitHub installs, suspicious registries
```

**Testing Plan**:
```bash
./paude
# Try to install package
npm install malicious-package  # Should fail or be restricted
pip install suspicious-lib  # Should fail or be restricted
```

**Acceptance Criteria**:
- [ ] Malicious packages cannot persist in container
- [ ] Or: Only approved packages can be installed
- [ ] Package installation audited/logged
- [ ] Clear error messages when blocked

---

## Next Steps After Completing Tasks

### Minimum Viable Security (Complete These First)
- ✅ TASK 4: Network Exfiltration Prevention
- ✅ TASK 5: Workspace Filesystem Protection (documentation-based)
- ✅ TASK 6: HTTPS Git Push Prevention (risk accepted)

### Recommended Before Semi-Autonomous Use
- TASK 7: Plugin System Audit
- TASK 8: Command Audit Logging
- TASK 9: Secrets Protection

### Final Hardening
- All remaining tasks

---

## Recovery & Emergency Procedures

### If Claude Goes Rogue

1. **Immediate**: Kill the container
   ```bash
   podman ps  # Find container ID
   podman kill <container-id>
   ```

2. **Assess Damage**:
   ```bash
   git status  # Check what changed
   git diff    # Review modifications
   cat /tmp/paude-audit-*.log  # Review commands executed
   ```

3. **Recover**:
   ```bash
   git reset --hard HEAD  # Discard all changes
   # OR restore from .git backup (Task 10)
   # OR restore from volume snapshot (Task 5)
   ```

4. **Investigate**:
   - Review audit logs to understand what happened
   - Check if data was exfiltrated (network logs if implemented)
   - Review conversation history to find prompt injection

---

## Session Workflow (Add to CLAUDE.md)

```markdown
## Security Hardening Workflow

We are systematically hardening paude security. Progress tracked in SECURITY-TASKS.md.

### Before Starting Security Work
1. Read SECURITY-TASKS.md to understand current progress
2. Pick next PENDING task in priority order
3. Create git branch for the task: `git checkout -b security/task-N`

### When Working on a Task
1. Mark task as IN PROGRESS in SECURITY-TASKS.md
2. Implement mitigation following task instructions
3. Run testing plan to verify
4. Update task status to COMPLETED with implementation notes
5. Commit changes: `git commit -m "Security: Complete Task N - [task name]"`
6. Merge to main

### After Completing a Task
1. Update Progress Summary at top of SECURITY-TASKS.md
2. Review if next task depends on this one
3. If all CRITICAL tasks done, evaluate comfort level with semi-autonomous use
```
