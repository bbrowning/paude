# Config Layering Feature

## Overview

This feature separates paude configuration into two distinct concerns:

1. **devcontainer.json** - Container setup (image, features, lifecycle commands)
2. **paude.json** - Security policy (network, credentials, restrictions)

This leverages the devcontainer ecosystem for standard container configuration while keeping paude focused on its unique value: security policy enforcement for Claude Code.

## Motivation

The devcontainer.json specification is a well-established standard for defining development containers. Many projects already have devcontainer.json files for VS Code, GitHub Codespaces, and JetBrains IDEs.

However, devcontainer.json cannot express the security policies paude needs:
- Network egress filtering
- Credential mount restrictions
- Command allowlists/denylists
- Audit logging

Rather than reinventing container configuration, paude should:
- Reuse devcontainer.json for container setup
- Focus paude.json on security policy

## Key Concepts

### Separation of Trust

| Config File | Trust Level | Purpose |
|-------------|-------------|---------|
| devcontainer.json | Untrusted (project) | What container to build |
| paude.json | Trusted (operator) | What security policy to apply |

### Restrictive Layering

When both global and project paude.json exist:
- Project config can only **restrict**, never expand
- Allowlists are **intersected** (must be in both)
- Boolean permissions are **AND**ed (both must allow)

### Security Properties

What paude.json controls (that devcontainer.json cannot):

```json
{
  "network": {
    "mode": "restricted",
    "allowlist": ["pypi.org", "npmjs.org"]
  },
  "credentials": {
    "gcloud": true,
    "ssh": false
  }
}
```

## Documentation

- [RESEARCH.md](RESEARCH.md) - Background research, prior art, ecosystem analysis
- [PLAN.md](PLAN.md) - Design decisions, schema definition, implementation approach
- [TASKS.md](TASKS.md) - Detailed implementation tasks with acceptance criteria

## Status

**Planning** - Design is complete, awaiting approval for implementation.

## Verification Checklist

Before considering this feature complete:

### Schema & Parsing
- [ ] JSON schema published and validates
- [ ] PaudeConfig model has all new fields
- [ ] Parser handles all new properties
- [ ] Invalid config produces helpful errors

### Network Policy
- [ ] Custom allowlist domains are accessible
- [ ] Non-allowlist domains are blocked
- [ ] `--network-allow` flag works
- [ ] Dry-run shows effective allowlist

### Credential Policy
- [ ] gcloud mount respects policy
- [ ] git_config mount respects policy
- [ ] SSH mount respects policy
- [ ] Warnings when credentials denied

### Configuration Layering
- [ ] Global config loaded from ~/.config/paude/
- [ ] Project config merges with global
- [ ] CLI flags override appropriately
- [ ] `--show-policy` displays effective config

### Backward Compatibility
- [ ] Existing paude.json files still work
- [ ] Deprecation warnings are clear
- [ ] Migration helper generates valid configs

### Documentation
- [ ] README updated with new config model
- [ ] Examples cover common use cases
- [ ] Migration guide is complete

## Example: Before and After

### Before (paude.json does everything)

```json
{
  "base": "python:3.12",
  "packages": ["ripgrep", "jq"],
  "setup": "pip install -e .",
  "venv": "auto"
}
```

### After (separation of concerns)

**.devcontainer/devcontainer.json** (container setup):
```json
{
  "image": "python:3.12",
  "features": {
    "ghcr.io/devcontainers/features/common-utils:2": {}
  },
  "postCreateCommand": "pip install -e ."
}
```

**.paude.json** (security policy):
```json
{
  "version": "1",
  "network": {
    "allowlist": ["pypi.org", "files.pythonhosted.org"]
  },
  "container": {
    "venv": "auto"
  }
}
```

## Decision Log

| Decision | Rationale |
|----------|-----------|
| Keep paude.json in repo | Teams need to share security policy |
| Restrictive-only merge | Prevents malicious repos from weakening security |
| Intersection for allowlists | Principle of least privilege |
| Deprecate container setup in paude.json | Reuse devcontainer ecosystem |
| Support global config | Users set personal security baseline |
