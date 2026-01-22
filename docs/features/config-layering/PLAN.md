# Config Layering: Design Plan

## Goal

Separate configuration concerns so that:
- **devcontainer.json** handles container setup (image, features, lifecycle commands)
- **paude.json** handles security policy (network, credentials, commands, audit)

This leverages the devcontainer ecosystem for standard container configuration while focusing paude on its unique value: security policy enforcement.

## Design Principles

### 1. Separation of Concerns

| Concern | Config File | Trust Level |
|---------|-------------|-------------|
| What container to build | devcontainer.json | Project (untrusted) |
| What security policy to apply | paude.json | Operator (trusted) |

### 2. Defense in Depth

Project configuration (devcontainer.json) is treated as untrusted input:
- Paude ignores security-relevant properties (mounts, runArgs, privileged)
- Paude applies its own security overlay regardless of project config
- Users are warned when security properties are detected and ignored

### 3. Additive Security Only

When paude.json is in the project repo:
- It can only restrict security policy, never expand it
- Global policy (~/.config/paude/) sets maximum permissions
- Project policy intersects with (narrows) global policy

## Schema Design

### paude.json v1 Schema

```json
{
  "$schema": "https://paude.dev/schema/v1.json",
  "version": "1",

  "network": {
    "mode": "restricted" | "open",
    "allowlist": ["domain.com", "*.googleapis.com"]
  },

  "credentials": {
    "gcloud": true | false,
    "git_config": true | false,
    "ssh": true | false,
    "gh_cli": true | false,
    "aws": true | false
  },

  "commands": {
    "deny": ["pattern1", "pattern2"]
  },

  "claude": {
    "yolo": true | false
  },

  "container": {
    "venv": "auto" | "none" | ["dir1", "dir2"],
    "pip_install": true | false | "custom command"
  },

  "audit": {
    "enabled": true | false,
    "log_path": "/path/to/audit.log"
  }
}
```

### Property Semantics

#### `network`

Controls egress filtering via squid proxy:

```json
{
  "network": {
    "mode": "restricted",
    "allowlist": [
      "*.googleapis.com",
      "pypi.org"
    ]
  }
}
```

- `mode: "restricted"` (default): Only allowlist domains accessible
- `mode: "open"`: All domains accessible (equivalent to --allow-network)
- `allowlist`: Domains to allow (glob patterns, applied only in restricted mode)

Default allowlist (always included in restricted mode):
- `*.googleapis.com` (Vertex AI)
- `*.google.com` (Vertex AI auth)

#### `credentials`

Controls what credentials are mounted into the container:

```json
{
  "credentials": {
    "gcloud": true,
    "git_config": true,
    "ssh": false,
    "gh_cli": false
  }
}
```

Defaults:
- `gcloud: true` (required for Vertex AI)
- `git_config: true` (commit attribution)
- `ssh: false` (prevents git push via SSH)
- `gh_cli: false` (prevents gh operations)

#### `commands`

Controls what commands Claude can execute:

```json
{
  "commands": {
    "deny": [
      "rm -rf /*",
      "curl * | *sh"
    ]
  }
}
```

This is enforced at the Claude level (not container level) and requires Claude Code support.

**Note**: This is a future consideration. Start with just the deny list pattern; actual enforcement depends on Claude Code hooks or wrapper scripts.

#### `claude`

Claude-specific behavior overrides:

```json
{
  "claude": {
    "yolo": false
  }
}
```

- `yolo: false` (default): Normal permission prompts
- `yolo: true`: Equivalent to passing --yolo

#### `container`

Paude-specific container behavior (migrated from current paude.json):

```json
{
  "container": {
    "venv": "auto",
    "pip_install": true
  }
}
```

These are paude-specific features that devcontainer.json cannot express.

#### `audit`

Audit logging for security-relevant operations:

```json
{
  "audit": {
    "enabled": true,
    "log_path": "~/.local/share/paude/audit.log"
  }
}
```

Logs:
- Container start/stop
- Network requests (via proxy logs)
- Commands executed (if hook available)

## Configuration Resolution

### Locations (in order of precedence)

1. **Command-line flags** (highest priority, session only)
   - `--allow-network` → `network.mode: "open"`
   - `--yolo` → `claude.yolo: true`

2. **Project config** (`.paude.json` in workspace root)
   - Can only restrict, not expand
   - Merged with global using intersection semantics

3. **Global config** (`~/.config/paude/policy.json`)
   - User's default security policy
   - Sets maximum allowed permissions

4. **Built-in defaults** (lowest priority)
   - Secure by default

### Merge Semantics

| Property Type | Merge Strategy | Example |
|---------------|----------------|---------|
| `network.allowlist` | Intersection | Global allows A,B,C; Project allows B,C,D → Result: B,C |
| `credentials.*` | AND (restrictive) | Global true, Project false → false |
| `commands.deny` | Union | Combined deny lists |
| `claude.yolo` | OR with warning | Either can enable, but warn if project enables |
| `container.*` | Project overrides | Paude-specific, project knows best |

### Example Resolution

Global (`~/.config/paude/policy.json`):
```json
{
  "network": {
    "mode": "restricted",
    "allowlist": ["*.googleapis.com", "*.google.com", "pypi.org", "npmjs.org"]
  },
  "credentials": {
    "gcloud": true,
    "git_config": true,
    "ssh": false
  }
}
```

Project (`.paude.json`):
```json
{
  "network": {
    "allowlist": ["pypi.org", "files.pythonhosted.org"]
  },
  "credentials": {
    "gcloud": true
  }
}
```

Result (effective policy):
```json
{
  "network": {
    "mode": "restricted",
    "allowlist": ["*.googleapis.com", "*.google.com", "pypi.org"]
    // Note: files.pythonhosted.org dropped (not in global)
    // Note: npmjs.org dropped (not in project)
  },
  "credentials": {
    "gcloud": true,
    "git_config": true,  // Global default
    "ssh": false         // Global restriction
  }
}
```

## devcontainer.json Integration

### Supported Properties (Container Setup)

| Property | Action |
|----------|--------|
| `image` | Use as base image |
| `build.dockerfile` | Build custom image |
| `build.context` | Build context path |
| `build.args` | Pass to docker build |
| `features` | Install dev container features |
| `postCreateCommand` | Run after container creation |
| `onCreateCommand` | Run on container creation |
| `updateContentCommand` | Run after content update |
| `postStartCommand` | Run after container start |
| `containerEnv` | Set environment variables |
| `remoteEnv` | Set environment variables |
| `initializeCommand` | Run on host before build |

### Blocked Properties (Security)

| Property | Reason |
|----------|--------|
| `mounts` | Paude controls mounts for security |
| `runArgs` | Could bypass security controls |
| `privileged` | Never allowed |
| `capAdd` | Capability restrictions enforced |
| `forwardPorts` | Network control via proxy |
| `appPort` | Network control via proxy |
| `overrideCommand` | Could bypass entrypoint |

### Detection and Precedence

```
workspace/
├── .devcontainer/
│   └── devcontainer.json    # Container setup (1st priority)
├── .devcontainer.json       # Container setup (2nd priority)
├── .paude.json              # Security policy
└── paude.json               # Legacy (deprecated)
```

## Migration Path

### Phase 1: Add Security Policy Support

1. Define new schema for security properties in paude.json
2. Add `network.allowlist` property
3. Add `credentials` property
4. Keep existing properties working (backward compatible)

### Phase 2: Deprecation Warnings

1. Warn when container setup properties found in paude.json:
   - `base` → use devcontainer.json `image`
   - `packages` → use devcontainer.json `features`
   - `setup` → use devcontainer.json `postCreateCommand`

2. Generate suggested devcontainer.json from deprecated properties

### Phase 3: Remove Deprecated Properties

1. Stop supporting container setup in paude.json
2. Require devcontainer.json for container customization
3. Keep paude.json focused on security policy

## CLI Changes

### New Flags

```bash
# Show effective policy
paude --show-policy

# Override network policy for session
paude --network-allow="custom.domain.com"

# Use specific policy file
paude --policy=/path/to/policy.json
```

### Dry-Run Output

```
$ paude --dry-run

Configuration:
  devcontainer.json: .devcontainer/devcontainer.json
  paude.json: .paude.json

Security Policy:
  Network: restricted
  Allowlist:
    - *.googleapis.com
    - *.google.com
    - pypi.org
  Credentials:
    - gcloud: yes (read-only)
    - git_config: yes (read-only)
    - ssh: no
    - gh_cli: no

Container Setup:
  Image: python:3.12
  Features: 1
  Post-create: pip install -e .
```

## Implementation Phases

### Phase 1: Schema Definition

- Define paude.json v1 schema
- Add JSON schema file for editor support
- Document schema in README

### Phase 2: Network Policy

- Add `network.allowlist` parsing
- Modify squid.conf generation to use allowlist
- Add `--network-allow` flag for session overrides

### Phase 3: Credential Policy

- Add `credentials` property parsing
- Make mount decisions based on credential policy
- Add warnings when credentials requested but denied

### Phase 4: Configuration Layering

- Add global config support (~/.config/paude/policy.json)
- Implement intersection merge logic
- Add `--show-policy` command

### Phase 5: Deprecation

- Add warnings for legacy paude.json properties
- Add migration helper (`paude migrate-config`)
- Document migration path

### Phase 6: Command Restrictions (Future)

- Define command pattern syntax
- Research enforcement mechanisms
- Depends on Claude Code hook support

## Security Considerations

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Malicious repo with permissive .paude.json | Intersection merge with global policy |
| Credential exfiltration | Default deny for sensitive credentials |
| Network exfiltration | Default restricted mode with allowlist |
| Config injection via symlinks | Resolve paths before reading |
| TOCTOU on config files | Read config once at startup |

### Audit Requirements

For security-sensitive deployments:
- Log all policy decisions
- Log effective policy at startup
- Log blocked network requests
- Log credential mount decisions

## Open Questions

1. **Should global policy be required or optional?**
   - Current thinking: Optional, with secure built-in defaults

2. **How to handle CLI flags vs config file conflicts?**
   - Current thinking: CLI flags override for session only

3. **Should .paude.json be .gitignore'd by default?**
   - Pro: Prevents accidental commit of local overrides
   - Con: Team can't share security policy
   - Consider: Template file (.paude.json.example)

4. **Environment variable overrides?**
   - PAUDE_NETWORK_MODE=open
   - PAUDE_ALLOW_SSH=true
   - Useful for CI but security risk

5. **Profile system for different contexts?**
   - `--profile=ci` for CI builds
   - `--profile=dev` for development
   - Consider for future phase

## Success Criteria

1. Users can customize network allowlist without code changes
2. Security-conscious users can restrict credentials further
3. Existing paude.json files continue to work (with deprecation warnings)
4. devcontainer.json files are reused for container setup
5. Security policy is auditable and predictable
