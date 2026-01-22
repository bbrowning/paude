# Config Layering: Implementation Tasks

## Phase 1: Schema Definition

### Task 1.1: Create JSON Schema

**Description**: Create a formal JSON schema for paude.json v1 format.

**Files to create**:
- `schemas/paude.v1.schema.json`

**Acceptance criteria**:
- [ ] Schema defines all properties from PLAN.md
- [ ] Schema validates correctly with ajv or similar
- [ ] Editor autocompletion works when $schema is set

**Example schema structure**:
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://paude.dev/schema/v1.json",
  "title": "Paude Configuration",
  "type": "object",
  "properties": {
    "version": { "const": "1" },
    "network": { "$ref": "#/$defs/network" },
    "credentials": { "$ref": "#/$defs/credentials" }
  }
}
```

### Task 1.2: Update PaudeConfig Model

**Description**: Add new security policy fields to PaudeConfig dataclass.

**Files to modify**:
- `src/paude/config/models.py`

**Changes**:
```python
@dataclass
class NetworkPolicy:
    mode: Literal["restricted", "open"] = "restricted"
    allowlist: list[str] = field(default_factory=list)

@dataclass
class CredentialPolicy:
    gcloud: bool = True
    git_config: bool = True
    ssh: bool = False
    gh_cli: bool = False

@dataclass
class PaudeConfig:
    # Existing fields...

    # New security policy fields
    network: NetworkPolicy = field(default_factory=NetworkPolicy)
    credentials: CredentialPolicy = field(default_factory=CredentialPolicy)
    audit_enabled: bool = False
```

**Acceptance criteria**:
- [ ] New dataclasses defined with defaults
- [ ] Type hints complete and mypy passes
- [ ] Existing tests still pass

### Task 1.3: Update Parser for New Schema

**Description**: Update parser to handle new paude.json properties.

**Files to modify**:
- `src/paude/config/parser.py`

**Changes**:
- Parse `network` object
- Parse `credentials` object
- Parse `audit` object
- Validate property types

**Acceptance criteria**:
- [ ] New properties parsed correctly
- [ ] Missing properties use defaults
- [ ] Invalid types raise ConfigError
- [ ] Tests cover all new properties

---

## Phase 2: Network Policy Implementation

### Task 2.1: Allowlist Configuration

**Description**: Make squid proxy use dynamic allowlist from config.

**Files to modify**:
- `src/paude/container/runner.py` (or new `proxy.py`)
- `containers/proxy/squid.conf` (template or dynamic)

**Changes**:
- Generate squid.conf from allowlist at runtime
- Always include base domains (googleapis.com, google.com)
- Support glob patterns in allowlist

**Acceptance criteria**:
- [ ] Custom allowlist domains accessible
- [ ] Domains not in allowlist blocked
- [ ] Base domains always allowed
- [ ] Glob patterns work (*.domain.com)

### Task 2.2: Network Mode Flag

**Description**: Add --network-allow flag for session allowlist additions.

**Files to modify**:
- `src/paude/cli.py`

**Changes**:
```python
@app.command()
def main(
    # Existing flags...
    network_allow: list[str] = typer.Option(
        [], "--network-allow",
        help="Additional domains to allow (session only)"
    ),
):
```

**Acceptance criteria**:
- [ ] Flag accepts multiple values
- [ ] Domains added to effective allowlist
- [ ] Shows in --dry-run output
- [ ] Does not persist to config

### Task 2.3: Dry-Run Shows Network Policy

**Description**: Update dry-run output to show effective network policy.

**Files to modify**:
- `src/paude/dry_run.py`

**Changes**:
- Add "Security Policy" section
- Show network mode and allowlist
- Show source of each allowlist entry (global/project/cli)

**Acceptance criteria**:
- [ ] Network mode displayed
- [ ] Allowlist domains listed
- [ ] Clear which config contributed each entry

---

## Phase 3: Credential Policy Implementation

### Task 3.1: Credential Mount Configuration

**Description**: Make mount decisions based on credential policy.

**Files to modify**:
- `src/paude/mounts.py`

**Changes**:
- Accept CredentialPolicy parameter
- Conditionally add/omit credential mounts
- Log which credentials are mounted

**Acceptance criteria**:
- [ ] gcloud mount controlled by policy
- [ ] git_config mount controlled by policy
- [ ] SSH key mount controlled by policy (when implemented)
- [ ] Tests for each credential type

### Task 3.2: Credential Policy Warnings

**Description**: Warn when credentials are denied that might be needed.

**Files to modify**:
- `src/paude/cli.py` or new `src/paude/warnings.py`

**Changes**:
- Detect when Vertex AI used without gcloud
- Warn if git operations might fail without git_config
- Suggest config changes

**Acceptance criteria**:
- [ ] Warning shown when gcloud disabled
- [ ] Warning shown when git_config disabled
- [ ] Warnings suppressible via flag

---

## Phase 4: Configuration Layering

### Task 4.1: Global Config Support

**Description**: Load global config from ~/.config/paude/policy.json.

**Files to modify**:
- `src/paude/config/detector.py`
- `src/paude/config/parser.py`

**Changes**:
- Check for global config file
- Load and parse global config
- Define merge order (global → project → cli)

**Acceptance criteria**:
- [ ] Global config loaded when present
- [ ] Missing global config uses defaults
- [ ] Global config path configurable via env var

### Task 4.2: Policy Merge Logic

**Description**: Implement intersection merge for security policies.

**Files to create**:
- `src/paude/config/merge.py`

**Changes**:
```python
def merge_policies(
    global_policy: PaudeConfig | None,
    project_policy: PaudeConfig | None,
    cli_overrides: dict,
) -> PaudeConfig:
    """Merge policies with intersection semantics."""
```

**Acceptance criteria**:
- [ ] Allowlist intersection works correctly
- [ ] Credential policies AND correctly
- [ ] CLI flags override appropriately
- [ ] Tests cover edge cases

### Task 4.3: Show Effective Policy Command

**Description**: Add --show-policy flag to display effective policy.

**Files to modify**:
- `src/paude/cli.py`

**Changes**:
- Add `--show-policy` flag
- Print merged policy as formatted output
- Show source of each setting (global/project/cli/default)

**Acceptance criteria**:
- [ ] Policy displayed in readable format
- [ ] Sources clearly indicated
- [ ] Works with --dry-run

---

## Phase 5: Deprecation and Migration

### Task 5.1: Deprecation Warnings

**Description**: Warn when legacy paude.json properties are used.

**Files to modify**:
- `src/paude/config/parser.py`

**Changes**:
- Detect `base`, `packages`, `setup` properties
- Emit deprecation warnings
- Suggest migration to devcontainer.json

**Acceptance criteria**:
- [ ] Warning for each deprecated property
- [ ] Specific migration suggestion provided
- [ ] Warnings suppressible via env var

### Task 5.2: Migration Helper

**Description**: Add command to generate devcontainer.json from legacy paude.json.

**Files to create**:
- `src/paude/migrate.py`

**Files to modify**:
- `src/paude/cli.py`

**Changes**:
- Add `paude migrate-config` subcommand
- Read legacy paude.json
- Generate equivalent devcontainer.json
- Generate new paude.json with security-only properties

**Acceptance criteria**:
- [ ] Generates valid devcontainer.json
- [ ] Preserves all functionality
- [ ] Non-destructive (writes new files)
- [ ] Shows diff of changes

### Task 5.3: Documentation Update

**Description**: Update all documentation for new config model.

**Files to modify**:
- `README.md`
- `CONTRIBUTING.md`
- `.claude/CLAUDE.md`

**Changes**:
- Document paude.json v1 schema
- Document devcontainer.json integration
- Document migration path
- Add examples

**Acceptance criteria**:
- [ ] README explains both config files
- [ ] Migration guide complete
- [ ] Examples cover common use cases

---

## Phase 6: Testing

### Task 6.1: Unit Tests for New Models

**Files to create/modify**:
- `tests/test_config.py`

**Tests to add**:
- NetworkPolicy defaults
- CredentialPolicy defaults
- Parsing new properties
- Invalid property handling

### Task 6.2: Unit Tests for Policy Merge

**Files to create**:
- `tests/test_merge.py`

**Tests to add**:
- Allowlist intersection
- Credential policy AND
- CLI overrides
- Empty policies
- Missing global config

### Task 6.3: Integration Tests

**Files to create/modify**:
- `tests/test_integration.py`

**Tests to add**:
- End-to-end with custom allowlist
- Credential policy affects mounts
- Migration command works
- Deprecation warnings appear

---

## Phase 7: Future Work (Not This Release)

### Task 7.1: Command Restrictions

**Description**: Implement command deny list enforcement.

**Dependencies**:
- Requires Claude Code hook support or wrapper script

**Approach options**:
- Shell wrapper that filters commands
- Claude Code pre-execution hook
- Container-level seccomp profile

### Task 7.2: Audit Logging

**Description**: Log security-relevant operations.

**Changes**:
- Log container start with effective policy
- Log proxy requests (from squid logs)
- Structured logging format (JSON)

### Task 7.3: Environment Variable Overrides

**Description**: Support env vars for common policy overrides.

**Examples**:
- `PAUDE_NETWORK_MODE=open`
- `PAUDE_ALLOW_SSH=true`

**Consideration**: Security implications in CI environments

---

## Dependency Graph

```
Phase 1 (Schema)
    │
    ├──► Phase 2 (Network Policy)
    │        │
    ├──► Phase 3 (Credential Policy)
    │        │
    └──► Phase 4 (Layering)
              │
              └──► Phase 5 (Deprecation)
                        │
                        └──► Phase 6 (Testing)
```

## Estimated Complexity

| Phase | Complexity | Notes |
|-------|------------|-------|
| Phase 1 | Low | Schema + dataclass changes |
| Phase 2 | Medium | Dynamic squid config generation |
| Phase 3 | Low | Conditional mount logic |
| Phase 4 | Medium | Merge logic has edge cases |
| Phase 5 | Low | Warnings + documentation |
| Phase 6 | Medium | Comprehensive test coverage |

## Rollback Plan

If issues arise:
1. Keep existing paude.json parsing unchanged
2. New properties are additive (backward compatible)
3. Feature flag for new merge behavior if needed
4. Document known issues and workarounds
