# BYOC Implementation Guide

This document provides instructions for implementing the BYOC (Bring Your Own Container) feature for paude. It is designed to guide a future Claude Code session through the implementation process.

## Overview

BYOC enables paude users to customize their container environment by specifying:
- Custom base images
- Custom Dockerfiles
- Dev container features (language runtimes, tools)
- Post-creation setup commands

This allows paude to work with any project type (Python, Go, Rust, etc.) while maintaining security guarantees.

## Documentation Structure

| Document | Purpose |
|----------|---------|
| `BYOC-RESEARCH.md` | Research findings on container customization solutions |
| `BYOC-PLAN.md` | Architecture and design decisions |
| `BYOC-TASKS.md` | Detailed implementation tasks with acceptance criteria |
| `BYOC-README.md` | This file - implementation guide |

## Before Starting

1. **Read all BYOC documents** in order: RESEARCH → PLAN → TASKS
2. **Understand current paude architecture**: Read `paude` script and `containers/paude/Dockerfile`
3. **Run existing tests**: `make test` to ensure baseline works
4. **Review CLAUDE.md**: Follow project conventions

## Implementation Approach

### Recommended Order

Start with Phase 1 tasks in order. Each task builds on the previous:

1. **Task 1.1**: Config detection - the foundation everything else needs
2. **Task 1.2**: Script refactoring - integrate config into main flow
3. **Task 1.3**: Simple image support - first user-visible feature
4. **Task 1.4**: Dockerfile support - second config option
5. **Task 1.5**: Caching - make builds fast

After Phase 1, proceed to Phase 2 for dev container features support.

### Code Organization

```
paude/
├── paude                    # Main script (modify)
├── lib/
│   ├── config.sh           # Config detection and parsing (create)
│   ├── hash.sh             # Config hashing for caching (create)
│   └── features.sh         # Dev container features (create, Phase 2)
├── containers/
│   └── paude/              # Existing container (may need base updates)
├── test/
│   ├── test_paude.sh       # Existing tests
│   ├── test_config.sh      # Config parsing tests (create)
│   └── fixtures/           # Test projects (create)
└── examples/               # Example configs (create)
```

### Key Implementation Details

#### JSON Parsing with jq

The container already has jq installed. Use it for all JSON parsing:

```bash
# Extract image property
image=$(jq -r '.image // empty' "$config_file")

# Extract nested property
dockerfile=$(jq -r '.build.dockerfile // empty' "$config_file")

# Check if property exists
if jq -e '.features' "$config_file" > /dev/null 2>&1; then
    # has features
fi
```

#### Dockerfile Generation

When generating a Dockerfile from user's config:

```bash
generate_dockerfile() {
    local base_image="$1"
    cat <<EOF
FROM $base_image

# Install required packages
RUN apt-get update && \\
    apt-get install -y git curl wget && \\
    rm -rf /var/lib/apt/lists/* || \\
    (apk add --no-cache git curl wget) || \\
    (yum install -y git curl wget)

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code || \\
    (curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \\
     apt-get install -y nodejs && \\
     npm install -g @anthropic-ai/claude-code)

# Create paude user
RUN useradd -m -s /bin/bash paude 2>/dev/null || \\
    adduser -D -s /bin/bash paude 2>/dev/null || true

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
EOF
}
```

#### Config Hash Calculation

For deterministic image tags:

```bash
compute_config_hash() {
    local config_file="$1"
    local hash_input=""

    # Include config file content
    hash_input+=$(cat "$config_file")

    # Include referenced Dockerfile if any
    local dockerfile=$(jq -r '.build.dockerfile // empty' "$config_file")
    if [[ -n "$dockerfile" ]]; then
        local config_dir=$(dirname "$config_file")
        local dockerfile_path="$config_dir/$dockerfile"
        if [[ -f "$dockerfile_path" ]]; then
            hash_input+=$(cat "$dockerfile_path")
        fi
    fi

    # Generate short hash
    echo "$hash_input" | sha256sum | cut -c1-12
}
```

## Testing Your Changes

### Manual Testing

```bash
# Test with no config (default behavior)
PAUDE_DEV=1 ./paude --version

# Create test config
mkdir -p /tmp/test-project/.devcontainer
echo '{"image": "python:3.11-slim"}' > /tmp/test-project/.devcontainer/devcontainer.json
cd /tmp/test-project
PAUDE_DEV=1 /path/to/paude

# Test config detection
cd /tmp/test-project
source /path/to/paude/lib/config.sh
detect_config
echo "Config: $PAUDE_CONFIG_FILE"
```

### Automated Tests

Add tests to `test/test_config.sh`:

```bash
test_no_config() {
    local tmpdir=$(mktemp -d)
    cd "$tmpdir"

    source "$SCRIPT_DIR/../lib/config.sh"
    detect_config

    assert_equals "" "$PAUDE_CONFIG_FILE" "Should be empty when no config"

    rm -rf "$tmpdir"
}

test_devcontainer_json() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"image": "python:3.11-slim"}' > "$tmpdir/.devcontainer/devcontainer.json"
    cd "$tmpdir"

    source "$SCRIPT_DIR/../lib/config.sh"
    detect_config

    assert_equals "$tmpdir/.devcontainer/devcontainer.json" "$PAUDE_CONFIG_FILE"

    rm -rf "$tmpdir"
}
```

## Kickoff Prompt

Use this prompt to start implementation:

---

**Prompt for Implementation Session:**

```
Implement the BYOC (Bring Your Own Container) feature for paude.

## Context
- BYOC-RESEARCH.md: Background on devcontainer.json and why we chose it
- BYOC-PLAN.md: Architecture, security model, and UX flows
- BYOC-TASKS.md: Step-by-step implementation with copy-paste code snippets

## Your First Task: Task 1.1

Create `lib/config.sh` following the exact code in BYOC-TASKS.md Task 1.1:

1. Create the lib directory: `mkdir -p lib`
2. Copy the `lib/config.sh` code from BYOC-TASKS.md exactly as written
3. Create `test/test_config.sh` with the test code from BYOC-TASKS.md
4. Run the tests: `bash test/test_config.sh`
5. Commit when tests pass: `git add lib/config.sh test/test_config.sh && git commit -m "Add BYOC config detection module"`

## Important Notes
- Each task has complete, copy-paste-ready code - use it directly
- Each task has an "Acceptance Criteria Checklist" - verify each item
- Run existing tests after each task: `make test`
- Commit after each task completes successfully

## Task Order
Complete tasks in order: 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 2.1 → etc.
Each task builds on the previous one.

Begin now with Task 1.1.
```

---

## Verification Checklist

Before considering implementation complete:

- [x] `make test` passes
- [x] `PAUDE_DEV=1 ./paude` works with no config (default behavior)
- [x] devcontainer.json with `image` property works
- [x] devcontainer.json with `build.dockerfile` works
- [x] Dev container features work (Phase 2)
- [x] postCreateCommand works (Phase 3)
- [x] Documentation updated in README.md
- [x] Example configs created in examples/

## Troubleshooting

### Common Issues

**jq not found**: Should be pre-installed. Check with `which jq`.

**podman build fails**: Check Dockerfile syntax. Use `podman build --no-cache` to debug.

**Features not downloading**: Network access required during build. Check proxy settings.

**User permissions**: Ensure generated Dockerfile creates non-root user correctly.

### Debug Mode

Add debug output:

```bash
# In paude script
if [[ "${PAUDE_DEBUG:-0}" == "1" ]]; then
    set -x
fi
```

Run with: `PAUDE_DEBUG=1 PAUDE_DEV=1 ./paude`

## Questions and Decisions

If you encounter design decisions not covered in the plan, consider:

1. **Security first**: Don't add features that weaken security model
2. **Simplicity**: Prefer simpler solutions
3. **Compatibility**: Match devcontainer.json spec behavior
4. **User experience**: Clear error messages, sensible defaults

Document any significant decisions made during implementation by adding notes to this file.
