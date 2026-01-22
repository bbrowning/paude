# Config Layering: Research

## Problem Statement

Paude currently conflates two distinct concerns in `paude.json`:
1. **Container setup** - image, features, environment, lifecycle commands
2. **Security policy** - network filtering, credential protection, command restrictions

The devcontainer.json specification already handles container setup well. Paude should leverage this instead of reinventing it, while focusing paude.json on the security policy layer that devcontainer.json cannot express.

## Current State

### What paude.json currently handles

```json
{
  "base": "python:3.12",        // Container setup (devcontainer has this)
  "packages": ["ripgrep"],      // Container setup (devcontainer features can do this)
  "setup": "pip install -e .",  // Container setup (postCreateCommand)
  "venv": "auto",               // Paude-specific (no devcontainer equivalent)
  "pip_install": true           // Paude-specific (no devcontainer equivalent)
}
```

### What devcontainer.json handles

From the [devcontainer.json reference](https://containers.dev/implementors/json_reference/):

| Property | Purpose | Currently Supported |
|----------|---------|---------------------|
| `image` | Base image | Yes |
| `build.dockerfile` | Custom Dockerfile | Yes |
| `build.context` | Build context | Yes |
| `build.args` | Build arguments | Yes |
| `features` | Dev container features | Yes |
| `postCreateCommand` | Post-create lifecycle | Yes |
| `containerEnv` | Environment variables | Yes |
| `initializeCommand` | Pre-build on host | No |
| `onCreateCommand` | After container creation | No |
| `updateContentCommand` | After content update | No |
| `postStartCommand` | After container start | No |
| `postAttachCommand` | After attach | No |
| `overrideFeatureInstallOrder` | Feature order | No |
| `mounts` | Volume mounts | Blocked (security) |
| `runArgs` | Container run args | Blocked (security) |
| `privileged` | Privileged mode | Blocked (security) |
| `capAdd` | Linux capabilities | Blocked (security) |

### What paude needs that devcontainer.json cannot express

1. **Network egress policy**
   - Allowlist of domains
   - Default deny
   - Proxy configuration

2. **Command restrictions**
   - Allowlist/blocklist of commands Claude can execute
   - Dangerous command protection

3. **Credential policy**
   - Which credentials to mount (gcloud, git, etc.)
   - Read-only vs read-write
   - Sensitive path protection

4. **Claude-specific settings**
   - Yolo mode defaults
   - Trust prompts configuration
   - Plugin policies

5. **Paude-specific container behavior**
   - venv isolation mode
   - pip install at build time
   - Audit logging

## Prior Art

### Kubernetes Security Model

Kubernetes separates concerns across multiple resources:

| Resource | Purpose | Analogy |
|----------|---------|---------|
| Deployment/Pod | What to run | devcontainer.json |
| NetworkPolicy | Network egress/ingress | paude.json network policy |
| PodSecurityPolicy | Capabilities, privileges | paude.json security policy |
| ServiceAccount | Identity, credentials | paude.json credential policy |
| RBAC | API access control | Not applicable (container) |

### Docker Compose + Security Profiles

Docker separates container definition from security:
- `docker-compose.yml` - What to run
- `seccomp` profiles - System call restrictions
- `apparmor`/`selinux` profiles - Access control

### Terraform: Resource vs Policy

Terraform separates infrastructure from policy:
- Resource definitions - What to create
- Sentinel policies - What's allowed (HashiCorp Enterprise)
- OPA policies - What's allowed (open source)

## Key Insight

All mature systems separate **"what you want to run"** from **"what you're allowed to run"**.

Paude should:
- Read devcontainer.json for "what to run" (reuse ecosystem)
- Read paude.json for "what's allowed" (security policy)
- Apply paude.json as a restrictive overlay that cannot be bypassed

## Compatibility Considerations

### devcontainer.json in project repo

Most projects already have or can adopt devcontainer.json:
- VS Code users can use it with Dev Containers extension
- JetBrains IDEs support it
- GitHub Codespaces uses it
- Paude can reuse it

### paude.json location options

| Location | Pros | Cons |
|----------|------|------|
| Project repo (`.paude.json`) | Version controlled, team-shared | Malicious repo could weaken security |
| User home (`~/.config/paude/`) | User controls policy | Not project-specific |
| Both (layered) | Flexible | Complex precedence |

### Proposed: Restrictive layering

```
~/.config/paude/policy.json      # Global defaults (user controls)
.paude.json                       # Project policy (can only restrict)
Command-line flags               # Session overrides (limited)
```

Key principle: **Project config can only restrict, never expand, global policy.**

## Security Implications

### Threat: Malicious project with permissive .paude.json

If `.paude.json` lives in the repo:
```json
{
  "network": {
    "allowlist": ["*"]  // Exfiltrate to anywhere!
  }
}
```

**Mitigation**: Project config is intersected with global policy, not unioned.

### Threat: Leaked credentials via mount policy

```json
{
  "credentials": {
    "mount_ssh": true  // Steal SSH keys!
  }
}
```

**Mitigation**: Project config cannot add credential mounts, only remove them.

### Threat: Command allowlist bypass

```json
{
  "commands": {
    "allow": ["*"]  // Run anything!
  }
}
```

**Mitigation**: Project config is intersected with global command policy.

## Schema Evolution

### Current paude.json (to be deprecated)

```json
{
  "base": "python:3.12",
  "packages": ["ripgrep"],
  "setup": "pip install -e .",
  "venv": "auto",
  "pip_install": true
}
```

### Proposed paude.json (security policy only)

```json
{
  "$schema": "https://paude.dev/schema/v1.json",
  "version": "1",

  "network": {
    "mode": "restricted",
    "allowlist": [
      "*.googleapis.com",
      "*.google.com",
      "pypi.org",
      "files.pythonhosted.org"
    ]
  },

  "commands": {
    "deny": [
      "rm -rf /",
      "curl * | sh"
    ]
  },

  "credentials": {
    "gcloud": true,
    "git_config": true,
    "ssh": false,
    "gh_cli": false
  },

  "claude": {
    "yolo": false,
    "trust_prompts": false
  },

  "venv": "auto",
  "pip_install": true,
  "audit_log": true
}
```

### Migration: Container setup moves to devcontainer.json

Before (paude.json only):
```json
{
  "base": "python:3.12",
  "packages": ["ripgrep"],
  "setup": "pip install -e ."
}
```

After (devcontainer.json + paude.json):
```json
// .devcontainer/devcontainer.json
{
  "image": "python:3.12",
  "features": {
    "ghcr.io/devcontainers/features/common-utils:2": {}
  },
  "postCreateCommand": "pip install -e ."
}

// .paude.json
{
  "version": "1",
  "network": {
    "allowlist": ["pypi.org", "files.pythonhosted.org"]
  },
  "venv": "auto"
}
```

## Open Questions

1. **Should paude.json live in the repo at all?**
   - Pro: Team shares security policy
   - Con: Malicious repo risk
   - Middle ground: Restrictive-only layering

2. **How to handle existing paude.json files?**
   - Deprecation period with warnings
   - Auto-migration tool
   - Support both formats during transition

3. **Should there be a global ~/.config/paude/policy.json?**
   - Pro: User sets baseline security
   - Con: Another config file to manage
   - Consider: Environment variables for common settings

4. **What about environment-specific policies?**
   - Development vs CI vs production
   - Profile system (--profile=ci)
   - Consider for future phase

5. **How to express command restrictions?**
   - Regex patterns
   - Glob patterns
   - Semantic categories ("no network tools", "no file deletion")
   - Consider: Start simple (deny list), expand later

## Recommendations

1. **Phase 1**: Define paude.json schema for security policy only
2. **Phase 2**: Move container setup properties to devcontainer.json with deprecation warnings
3. **Phase 3**: Implement restrictive layering for project vs global policy
4. **Phase 4**: Add command restrictions and audit logging

## References

- [Devcontainer.json Reference](https://containers.dev/implementors/json_reference/)
- [Kubernetes Network Policy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
- [Open Policy Agent](https://www.openpolicyagent.org/)
- [Claude Code Devcontainer Docs](https://code.claude.com/docs/en/devcontainer)
