# BYOC Implementation Tasks

This document contains detailed implementation tasks for the BYOC (Bring Your Own Container) feature. Tasks are written to be implementable by a junior engineer with clear step-by-step instructions.

## Prerequisites

Before starting:
- Run `make test` to verify baseline works
- Read the existing `paude` script to understand the flow
- Ensure jq is available: `which jq`
- Understand that paude uses podman (not docker)

## Conventions Used in This Document

- **Global variables**: UPPERCASE (e.g., `PAUDE_CONFIG_FILE`)
- **Local variables**: lowercase (e.g., `local config_file`)
- **Functions**: snake_case (e.g., `detect_config`)
- **Exit codes**: 0 = success, 1 = error
- **Output**: Use `>&2` for errors/warnings, stdout for data

---

## Phase 1: Foundation

### Task 1.1: Create config detection module

**Goal**: Create `lib/config.sh` that detects and parses devcontainer.json.

**Step 1**: Create the lib directory
```bash
mkdir -p lib
```

**Step 2**: Create `lib/config.sh` with these exact functions:

```bash
#!/bin/bash
# lib/config.sh - Configuration detection and parsing for BYOC

# === Global Variables (initialize at top of file) ===
PAUDE_CONFIG_FILE=""
PAUDE_CONFIG_TYPE="default"
PAUDE_BASE_IMAGE=""
PAUDE_DOCKERFILE=""
PAUDE_BUILD_CONTEXT=""

# === Functions ===

detect_config() {
    # Check locations in priority order
    local workspace="${1:-$(pwd)}"

    PAUDE_CONFIG_FILE=""
    PAUDE_CONFIG_TYPE="default"

    if [[ -f "$workspace/.devcontainer/devcontainer.json" ]]; then
        PAUDE_CONFIG_FILE="$workspace/.devcontainer/devcontainer.json"
        PAUDE_CONFIG_TYPE="devcontainer"
    elif [[ -f "$workspace/.devcontainer.json" ]]; then
        PAUDE_CONFIG_FILE="$workspace/.devcontainer.json"
        PAUDE_CONFIG_TYPE="devcontainer"
    elif [[ -f "$workspace/paude.json" ]]; then
        PAUDE_CONFIG_FILE="$workspace/paude.json"
        PAUDE_CONFIG_TYPE="paude"
    fi

    if [[ -n "$PAUDE_CONFIG_FILE" ]]; then
        echo "Detected $PAUDE_CONFIG_TYPE config: $PAUDE_CONFIG_FILE" >&2
    fi
}

parse_config() {
    # Call after detect_config(). Parses the config file and sets global variables.
    # Returns 0 on success, 1 on error.

    if [[ -z "$PAUDE_CONFIG_FILE" ]]; then
        # No config, use defaults
        PAUDE_BASE_IMAGE=""
        PAUDE_DOCKERFILE=""
        PAUDE_BUILD_CONTEXT=""
        return 0
    fi

    # Validate JSON
    if ! jq empty "$PAUDE_CONFIG_FILE" 2>/dev/null; then
        echo "Error: Invalid JSON in $PAUDE_CONFIG_FILE" >&2
        return 1
    fi

    if [[ "$PAUDE_CONFIG_TYPE" == "devcontainer" ]]; then
        _parse_devcontainer
    elif [[ "$PAUDE_CONFIG_TYPE" == "paude" ]]; then
        _parse_paude_json
    fi
}

_parse_devcontainer() {
    local config_dir
    config_dir=$(dirname "$PAUDE_CONFIG_FILE")

    # Extract image (direct image reference)
    PAUDE_BASE_IMAGE=$(jq -r '.image // empty' "$PAUDE_CONFIG_FILE")

    # Extract dockerfile path (for custom builds)
    PAUDE_DOCKERFILE=$(jq -r '.build.dockerfile // empty' "$PAUDE_CONFIG_FILE")
    if [[ -n "$PAUDE_DOCKERFILE" ]]; then
        # Make path absolute relative to config file location
        if [[ ! "$PAUDE_DOCKERFILE" = /* ]]; then
            PAUDE_DOCKERFILE="$config_dir/$PAUDE_DOCKERFILE"
        fi
    fi

    # Extract build context
    PAUDE_BUILD_CONTEXT=$(jq -r '.build.context // empty' "$PAUDE_CONFIG_FILE")
    if [[ -n "$PAUDE_BUILD_CONTEXT" ]]; then
        if [[ ! "$PAUDE_BUILD_CONTEXT" = /* ]]; then
            PAUDE_BUILD_CONTEXT="$config_dir/$PAUDE_BUILD_CONTEXT"
        fi
    elif [[ -n "$PAUDE_DOCKERFILE" ]]; then
        # Default context is the directory containing devcontainer.json
        PAUDE_BUILD_CONTEXT="$config_dir"
    fi

    # Warn about unsupported properties
    _warn_unsupported_properties
}

_parse_paude_json() {
    # paude.json uses "base" instead of "image"
    PAUDE_BASE_IMAGE=$(jq -r '.base // empty' "$PAUDE_CONFIG_FILE")
    PAUDE_DOCKERFILE=""
    PAUDE_BUILD_CONTEXT=""
}

_warn_unsupported_properties() {
    local unsupported=("mounts" "runArgs" "privileged" "capAdd" "forwardPorts" "remoteUser")
    for prop in "${unsupported[@]}"; do
        if jq -e ".$prop" "$PAUDE_CONFIG_FILE" >/dev/null 2>&1; then
            echo "Warning: Ignoring unsupported property '$prop' in config" >&2
            echo "  → paude controls this for security" >&2
        fi
    done
}

has_custom_config() {
    # Returns 0 if custom config exists, 1 otherwise
    [[ -n "$PAUDE_CONFIG_FILE" ]]
}

needs_custom_build() {
    # Returns 0 if we need to build a custom image, 1 otherwise
    [[ -n "$PAUDE_BASE_IMAGE" || -n "$PAUDE_DOCKERFILE" ]]
}
```

**Step 3**: Write tests in `test/test_config.sh`:

```bash
#!/bin/bash
# test/test_config.sh - Unit tests for config detection

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/config.sh"

TESTS_RUN=0
TESTS_PASSED=0

assert_equals() {
    local expected="$1"
    local actual="$2"
    local message="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$expected" == "$actual" ]]; then
        echo "✓ $message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "✗ $message"
        echo "  Expected: '$expected'"
        echo "  Actual:   '$actual'"
    fi
}

test_no_config() {
    local tmpdir=$(mktemp -d)
    detect_config "$tmpdir"
    assert_equals "" "$PAUDE_CONFIG_FILE" "No config file detected in empty dir"
    assert_equals "default" "$PAUDE_CONFIG_TYPE" "Config type is default"
    rm -rf "$tmpdir"
}

test_devcontainer_in_folder() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"image": "python:3.11-slim"}' > "$tmpdir/.devcontainer/devcontainer.json"

    detect_config "$tmpdir"
    assert_equals "$tmpdir/.devcontainer/devcontainer.json" "$PAUDE_CONFIG_FILE" "Detects .devcontainer/devcontainer.json"
    assert_equals "devcontainer" "$PAUDE_CONFIG_TYPE" "Config type is devcontainer"

    parse_config
    assert_equals "python:3.11-slim" "$PAUDE_BASE_IMAGE" "Parses image property"

    rm -rf "$tmpdir"
}

test_devcontainer_at_root() {
    local tmpdir=$(mktemp -d)
    echo '{"image": "node:20-slim"}' > "$tmpdir/.devcontainer.json"

    detect_config "$tmpdir"
    assert_equals "$tmpdir/.devcontainer.json" "$PAUDE_CONFIG_FILE" "Detects .devcontainer.json at root"

    rm -rf "$tmpdir"
}

test_paude_json() {
    local tmpdir=$(mktemp -d)
    echo '{"base": "golang:1.21"}' > "$tmpdir/paude.json"

    detect_config "$tmpdir"
    assert_equals "$tmpdir/paude.json" "$PAUDE_CONFIG_FILE" "Detects paude.json"
    assert_equals "paude" "$PAUDE_CONFIG_TYPE" "Config type is paude"

    parse_config
    assert_equals "golang:1.21" "$PAUDE_BASE_IMAGE" "Parses base property from paude.json"

    rm -rf "$tmpdir"
}

test_dockerfile_path() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"build": {"dockerfile": "Dockerfile", "context": ".."}}' > "$tmpdir/.devcontainer/devcontainer.json"
    echo 'FROM ubuntu:22.04' > "$tmpdir/.devcontainer/Dockerfile"

    detect_config "$tmpdir"
    parse_config

    assert_equals "$tmpdir/.devcontainer/Dockerfile" "$PAUDE_DOCKERFILE" "Resolves dockerfile path"
    assert_equals "$tmpdir" "$PAUDE_BUILD_CONTEXT" "Resolves build context"

    rm -rf "$tmpdir"
}

test_invalid_json() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo 'not valid json' > "$tmpdir/.devcontainer/devcontainer.json"

    detect_config "$tmpdir"
    if ! parse_config; then
        echo "✓ Returns error for invalid JSON"
        TESTS_RUN=$((TESTS_RUN + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "✗ Should return error for invalid JSON"
        TESTS_RUN=$((TESTS_RUN + 1))
    fi

    rm -rf "$tmpdir"
}

test_priority_order() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"image": "priority1"}' > "$tmpdir/.devcontainer/devcontainer.json"
    echo '{"image": "priority2"}' > "$tmpdir/.devcontainer.json"
    echo '{"base": "priority3"}' > "$tmpdir/paude.json"

    detect_config "$tmpdir"
    parse_config
    assert_equals "priority1" "$PAUDE_BASE_IMAGE" ".devcontainer/devcontainer.json takes priority"

    rm -rf "$tmpdir"
}

# Run all tests
echo "Running config module tests..."
echo ""
test_no_config
test_devcontainer_in_folder
test_devcontainer_at_root
test_paude_json
test_dockerfile_path
test_invalid_json
test_priority_order

echo ""
echo "Tests: $TESTS_PASSED/$TESTS_RUN passed"
[[ $TESTS_PASSED -eq $TESTS_RUN ]]
```

**Step 4**: Add to Makefile:
```makefile
test-config:
	@bash test/test_config.sh
```

**Acceptance Criteria Checklist**:
- [x] `lib/config.sh` exists and is executable
- [x] `test/test_config.sh` passes all tests
- [x] `detect_config` finds files in correct priority order
- [x] `parse_config` extracts image, dockerfile, and context
- [x] Invalid JSON returns error code 1

---

### Task 1.2: Integrate config module into paude script

**Goal**: Modify the `paude` script to detect and use custom configurations.

**Step 1**: Add these lines near the top of `paude` (after SCRIPT_DIR is set):
```bash
# Export script dir for library modules (they need to find containers/paude/entrypoint.sh)
PAUDE_SCRIPT_DIR="$SCRIPT_DIR"

# Source library modules
source "$SCRIPT_DIR/lib/config.sh"
```

**Step 2**: Add `--rebuild` flag to argument parsing (in the case statement):
```bash
--rebuild)
    FORCE_REBUILD=true
    ;;
```

And initialize the variable at the top with other flags:
```bash
FORCE_REBUILD=false
```

**Step 3**: Add `--rebuild` to `show_help()`:
```bash
    --rebuild           Force rebuild of workspace container image
                        Use when devcontainer.json has changed
```

**Step 4**: Call config detection before `ensure_images()`. Add this after `check_requirements`:
```bash
# Detect workspace configuration
detect_config "$WORKSPACE_DIR"
if has_custom_config; then
    parse_config || exit 1
fi
```

Wait - WORKSPACE_DIR isn't set until `setup_mounts()`. We need to restructure the flow:

**Corrected Step 4**: Restructure main execution at bottom of script:
```bash
# Main execution
check_requirements

# Setup workspace dir early (needed for config detection)
WORKSPACE_DIR="$(pwd -P)"

# Detect and parse configuration
detect_config "$WORKSPACE_DIR"
if has_custom_config; then
    parse_config || exit 1
fi

ensure_images
setup_environment
setup_mounts
check_macos_volumes
check_git_safety

if [[ "$ALLOW_NETWORK" == "false" ]]; then
    setup_proxy
fi

run_claude
```

**Step 5**: Modify `ensure_images()` to handle custom configs. This is covered in Task 1.3, but for now, just verify it still works with the default case.

**Acceptance Criteria Checklist**:
- [x] `paude --help` shows `--rebuild` flag
- [x] `paude --version` still works
- [x] Running `paude` in a directory with no config uses default image
- [x] Running `paude` in a directory with devcontainer.json prints detection message
- [x] `PAUDE_DEV=1 ./paude --version` works

---

### Task 1.3: Implement simple image support

**Goal**: When devcontainer.json specifies an `image`, build a derived image that adds paude requirements.

**Step 1**: Create a build directory for generated Dockerfiles:
```bash
# In paude script, add this function:
prepare_build_dir() {
    PAUDE_BUILD_DIR="${TMPDIR:-/tmp}/paude-build-$$"
    mkdir -p "$PAUDE_BUILD_DIR"
    # Copy entrypoint to build context
    cp "$SCRIPT_DIR/containers/paude/entrypoint.sh" "$PAUDE_BUILD_DIR/"
}

cleanup_build_dir() {
    if [[ -n "$PAUDE_BUILD_DIR" && -d "$PAUDE_BUILD_DIR" ]]; then
        rm -rf "$PAUDE_BUILD_DIR"
    fi
}
```

**Step 2**: Create the Dockerfile generation function in `lib/config.sh`:

```bash
generate_workspace_dockerfile() {
    # Generates a Dockerfile that wraps the user's base image with paude requirements
    # Output: writes Dockerfile content to stdout
    local base_image="$1"
    local build_dir="$2"

    cat <<'DOCKERFILE'
# Auto-generated by paude - DO NOT EDIT
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# Install required system packages
# Try apt-get (Debian/Ubuntu), then apk (Alpine), then yum (RHEL/CentOS)
RUN if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends git curl ca-certificates && \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache git curl ca-certificates; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y git curl ca-certificates && \
        yum clean all; \
    else \
        echo "Warning: Unknown package manager, git may not be available" >&2; \
    fi

# Install Node.js if not present (required for claude-code)
RUN if ! command -v node >/dev/null 2>&1; then \
        if command -v apt-get >/dev/null 2>&1; then \
            curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
            apt-get install -y nodejs && \
            rm -rf /var/lib/apt/lists/*; \
        elif command -v apk >/dev/null 2>&1; then \
            apk add --no-cache nodejs npm; \
        else \
            echo "Error: Cannot install Node.js - unsupported base image" >&2 && exit 1; \
        fi \
    fi

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Create paude user if it doesn't exist
RUN id paude >/dev/null 2>&1 || useradd -m -s /bin/bash paude 2>/dev/null || adduser -D -s /bin/bash paude

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
DOCKERFILE
}
```

**Step 3**: Modify `ensure_images()` to handle custom images. Replace the function with:

```bash
ensure_images() {
    if needs_custom_build; then
        ensure_custom_image
    else
        ensure_default_image
    fi

    # Proxy image logic remains the same
    if [[ "$ALLOW_NETWORK" == "false" ]]; then
        ensure_proxy_image
    fi
}

ensure_default_image() {
    if [[ "$DEV_MODE" == "true" ]]; then
        if ! podman image exists "$IMAGE_NAME" 2>/dev/null; then
            echo "Building $IMAGE_NAME image..."
            if ! podman build -t "$IMAGE_NAME" "$SCRIPT_DIR/containers/paude"; then
                echo "Error: Failed to build $IMAGE_NAME" >&2
                exit 1
            fi
        fi
    else
        if ! podman image exists "$IMAGE_NAME" 2>/dev/null; then
            echo "Pulling $IMAGE_NAME..."
            if ! podman pull "$IMAGE_NAME"; then
                echo "Error: Failed to pull $IMAGE_NAME" >&2
                exit 1
            fi
        fi
    fi
}

ensure_custom_image() {
    # Compute image tag based on config hash
    source "$SCRIPT_DIR/lib/hash.sh"
    local config_hash
    config_hash=$(compute_config_hash)
    local custom_image="paude-workspace:$config_hash"

    # Check if image already exists and rebuild not forced
    if podman image exists "$custom_image" 2>/dev/null && [[ "$FORCE_REBUILD" != "true" ]]; then
        echo "Using cached workspace image: $custom_image" >&2
        IMAGE_NAME="$custom_image"
        return 0
    fi

    echo "Building workspace image..." >&2
    prepare_build_dir
    trap cleanup_build_dir EXIT

    if [[ -n "$PAUDE_BASE_IMAGE" ]]; then
        # Generate Dockerfile for image-based config
        generate_workspace_dockerfile "$PAUDE_BASE_IMAGE" "$PAUDE_BUILD_DIR" > "$PAUDE_BUILD_DIR/Dockerfile"

        echo "  → Using base: $PAUDE_BASE_IMAGE" >&2
        if ! podman build \
            --build-arg "BASE_IMAGE=$PAUDE_BASE_IMAGE" \
            -t "$custom_image" \
            "$PAUDE_BUILD_DIR"; then
            echo "Error: Failed to build workspace image" >&2
            exit 1
        fi
    elif [[ -n "$PAUDE_DOCKERFILE" ]]; then
        # This is handled in Task 1.4
        echo "Error: Dockerfile builds not yet implemented" >&2
        exit 1
    fi

    echo "Build complete (cached as $custom_image)" >&2
    IMAGE_NAME="$custom_image"
}

ensure_proxy_image() {
    if [[ "$DEV_MODE" == "true" ]]; then
        if ! podman image exists "$PROXY_IMAGE" 2>/dev/null; then
            echo "Building $PROXY_IMAGE image..."
            if ! podman build -t "$PROXY_IMAGE" "$SCRIPT_DIR/containers/proxy"; then
                echo "Error: Failed to build $PROXY_IMAGE" >&2
                exit 1
            fi
        fi
    else
        if ! podman image exists "$PROXY_IMAGE" 2>/dev/null; then
            echo "Pulling $PROXY_IMAGE..."
            if ! podman pull "$PROXY_IMAGE"; then
                echo "Error: Failed to pull $PROXY_IMAGE" >&2
                exit 1
            fi
        fi
    fi
}
```

**Step 4**: Test with a sample project:
```bash
# Create test project
mkdir -p /tmp/test-python/.devcontainer
cat > /tmp/test-python/.devcontainer/devcontainer.json << 'EOF'
{
    "image": "python:3.11-slim"
}
EOF

# Test
cd /tmp/test-python
PAUDE_DEV=1 /path/to/paude --version
# Should show: building workspace image, using base python:3.11-slim
```

**Acceptance Criteria Checklist**:
- [x] `paude` in directory with `{"image": "python:3.11-slim"}` builds a custom image
- [x] Built image is cached and reused on subsequent runs
- [x] `paude --rebuild` forces a new build
- [x] Claude Code is installed in the custom image
- [x] The paude user exists in the custom image

---

### Task 1.4: Implement Dockerfile build support

**Goal**: Support `build.dockerfile` property to build from user's Dockerfile.

**Step 1**: Update `ensure_custom_image()` to handle Dockerfile builds. Replace the elif block:

```bash
elif [[ -n "$PAUDE_DOCKERFILE" ]]; then
    if [[ ! -f "$PAUDE_DOCKERFILE" ]]; then
        echo "Error: Dockerfile not found: $PAUDE_DOCKERFILE" >&2
        exit 1
    fi

    echo "  → Building from: $PAUDE_DOCKERFILE" >&2

    # Create a combined Dockerfile that extends user's Dockerfile
    # We use a multi-stage build to add paude requirements

    # First, copy user's Dockerfile
    cp "$PAUDE_DOCKERFILE" "$PAUDE_BUILD_DIR/Dockerfile.user"

    # Generate wrapper that builds on top
    cat > "$PAUDE_BUILD_DIR/Dockerfile" << 'WRAPPER_EOF'
# Stage 1: User's original Dockerfile
FROM user-base AS user-stage

# Stage 2: Add paude requirements
FROM user-stage

# Install required system packages
RUN if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends git curl ca-certificates && \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache git curl ca-certificates; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y git curl ca-certificates && \
        yum clean all; \
    fi

# Install Node.js if not present
RUN if ! command -v node >/dev/null 2>&1; then \
        if command -v apt-get >/dev/null 2>&1; then \
            curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
            apt-get install -y nodejs && \
            rm -rf /var/lib/apt/lists/*; \
        elif command -v apk >/dev/null 2>&1; then \
            apk add --no-cache nodejs npm; \
        fi \
    fi

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Create paude user
RUN id paude >/dev/null 2>&1 || useradd -m -s /bin/bash paude 2>/dev/null || adduser -D -s /bin/bash paude

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
WRAPPER_EOF

    # Actually, the multi-stage approach above won't work directly.
    # Simpler approach: build user's Dockerfile first, then extend it

    # Build user's image first
    local user_image="paude-user-base:$config_hash"
    echo "  → Building user Dockerfile..." >&2

    local build_context="${PAUDE_BUILD_CONTEXT:-$(dirname "$PAUDE_DOCKERFILE")}"

    if ! podman build \
        -t "$user_image" \
        -f "$PAUDE_DOCKERFILE" \
        "$build_context"; then
        echo "Error: Failed to build user Dockerfile" >&2
        exit 1
    fi

    # Now generate and build paude wrapper
    generate_workspace_dockerfile "$user_image" "$PAUDE_BUILD_DIR" > "$PAUDE_BUILD_DIR/Dockerfile"

    echo "  → Adding paude requirements..." >&2
    if ! podman build \
        --build-arg "BASE_IMAGE=$user_image" \
        -t "$custom_image" \
        "$PAUDE_BUILD_DIR"; then
        echo "Error: Failed to build workspace image" >&2
        exit 1
    fi
fi
```

**Step 2**: Handle `build.args` by extracting and passing them:

Add to `lib/config.sh`:
```bash
parse_build_args() {
    # Returns build args as "--build-arg KEY=VALUE" strings
    # Usage: build_args=$(parse_build_args)
    if [[ -z "$PAUDE_CONFIG_FILE" ]]; then
        return
    fi

    jq -r '.build.args // {} | to_entries[] | "--build-arg \(.key)=\(.value)"' "$PAUDE_CONFIG_FILE" 2>/dev/null || true
}
```

Then use it in the user image build:
```bash
local build_args
build_args=$(parse_build_args)

if ! podman build \
    -t "$user_image" \
    -f "$PAUDE_DOCKERFILE" \
    $build_args \
    "$build_context"; then
```

**Step 3**: Test with a custom Dockerfile:
```bash
# Create test project
mkdir -p /tmp/test-custom/.devcontainer
cat > /tmp/test-custom/.devcontainer/Dockerfile << 'EOF'
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y vim
EOF

cat > /tmp/test-custom/.devcontainer/devcontainer.json << 'EOF'
{
    "build": {
        "dockerfile": "Dockerfile"
    }
}
EOF

# Test
cd /tmp/test-custom
PAUDE_DEV=1 /path/to/paude --version
```

**Acceptance Criteria Checklist**:
- [x] `build.dockerfile` path is resolved correctly (relative to devcontainer.json)
- [x] User's Dockerfile is built first
- [x] paude requirements are layered on top
- [x] `build.context` is respected
- [x] `build.args` are passed to the build

---

### Task 1.5: Add config hash for caching

**Goal**: Create `lib/hash.sh` with deterministic hash computation for image caching.

**Step 1**: Create `lib/hash.sh`:

```bash
#!/bin/bash
# lib/hash.sh - Compute deterministic hash for config caching

compute_config_hash() {
    # Computes a hash of the configuration for image tagging
    # Includes: config file content, referenced Dockerfile content
    # Returns: 12-character hash string

    local hash_input=""

    # Include config file content
    if [[ -n "$PAUDE_CONFIG_FILE" && -f "$PAUDE_CONFIG_FILE" ]]; then
        hash_input+=$(cat "$PAUDE_CONFIG_FILE")
    fi

    # Include Dockerfile content if referenced
    if [[ -n "$PAUDE_DOCKERFILE" && -f "$PAUDE_DOCKERFILE" ]]; then
        hash_input+=$(cat "$PAUDE_DOCKERFILE")
    fi

    # Include base image name (for image-only configs)
    if [[ -n "$PAUDE_BASE_IMAGE" ]]; then
        hash_input+="$PAUDE_BASE_IMAGE"
    fi

    # Include entrypoint.sh (changes to this should trigger rebuild)
    # Note: PAUDE_SCRIPT_DIR should be set by the main paude script before sourcing
    if [[ -n "$PAUDE_SCRIPT_DIR" ]]; then
        local entrypoint="$PAUDE_SCRIPT_DIR/containers/paude/entrypoint.sh"
        if [[ -f "$entrypoint" ]]; then
            hash_input+=$(cat "$entrypoint")
        fi
    fi

    # Generate hash - use sha256sum and take first 12 chars
    if command -v sha256sum >/dev/null 2>&1; then
        echo "$hash_input" | sha256sum | cut -c1-12
    elif command -v shasum >/dev/null 2>&1; then
        # macOS
        echo "$hash_input" | shasum -a 256 | cut -c1-12
    else
        # Fallback: use md5
        echo "$hash_input" | md5sum | cut -c1-12
    fi
}

is_image_stale() {
    # Check if the current config hash matches an existing image
    # Returns 0 if stale (needs rebuild), 1 if fresh
    local current_hash
    current_hash=$(compute_config_hash)
    local image_name="paude-workspace:$current_hash"

    if podman image exists "$image_name" 2>/dev/null; then
        return 1  # Image exists, not stale
    else
        return 0  # Image doesn't exist, stale
    fi
}
```

**Step 2**: Write tests in `test/test_hash.sh`:

```bash
#!/bin/bash
# test/test_hash.sh - Unit tests for hash computation

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/config.sh"
source "$SCRIPT_DIR/../lib/hash.sh"

TESTS_RUN=0
TESTS_PASSED=0

assert_equals() {
    local expected="$1"
    local actual="$2"
    local message="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$expected" == "$actual" ]]; then
        echo "✓ $message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "✗ $message"
        echo "  Expected: '$expected'"
        echo "  Actual:   '$actual'"
    fi
}

assert_not_equals() {
    local unexpected="$1"
    local actual="$2"
    local message="$3"
    TESTS_RUN=$((TESTS_RUN + 1))
    if [[ "$unexpected" != "$actual" ]]; then
        echo "✓ $message"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "✗ $message"
        echo "  Should not equal: '$unexpected'"
    fi
}

test_same_config_same_hash() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"image": "python:3.11-slim"}' > "$tmpdir/.devcontainer/devcontainer.json"

    detect_config "$tmpdir"
    parse_config
    local hash1=$(compute_config_hash)
    local hash2=$(compute_config_hash)

    assert_equals "$hash1" "$hash2" "Same config produces same hash"
    rm -rf "$tmpdir"
}

test_different_config_different_hash() {
    local tmpdir1=$(mktemp -d)
    local tmpdir2=$(mktemp -d)
    mkdir -p "$tmpdir1/.devcontainer" "$tmpdir2/.devcontainer"
    echo '{"image": "python:3.11-slim"}' > "$tmpdir1/.devcontainer/devcontainer.json"
    echo '{"image": "python:3.12-slim"}' > "$tmpdir2/.devcontainer/devcontainer.json"

    detect_config "$tmpdir1"
    parse_config
    local hash1=$(compute_config_hash)

    detect_config "$tmpdir2"
    parse_config
    local hash2=$(compute_config_hash)

    assert_not_equals "$hash1" "$hash2" "Different configs produce different hashes"
    rm -rf "$tmpdir1" "$tmpdir2"
}

test_hash_length() {
    local tmpdir=$(mktemp -d)
    mkdir -p "$tmpdir/.devcontainer"
    echo '{"image": "python:3.11-slim"}' > "$tmpdir/.devcontainer/devcontainer.json"

    detect_config "$tmpdir"
    parse_config
    local hash=$(compute_config_hash)

    if [[ ${#hash} -eq 12 ]]; then
        echo "✓ Hash is 12 characters"
        TESTS_RUN=$((TESTS_RUN + 1))
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        echo "✗ Hash should be 12 characters, got ${#hash}"
        TESTS_RUN=$((TESTS_RUN + 1))
    fi
    rm -rf "$tmpdir"
}

# Run tests
echo "Running hash module tests..."
echo ""
test_same_config_same_hash
test_different_config_different_hash
test_hash_length

echo ""
echo "Tests: $TESTS_PASSED/$TESTS_RUN passed"
[[ $TESTS_PASSED -eq $TESTS_RUN ]]
```

**Acceptance Criteria Checklist**:
- [x] `lib/hash.sh` exists and is sourceable
- [x] Same config always produces same hash
- [x] Different configs produce different hashes
- [x] Hash is 12 characters long
- [x] Changing Dockerfile content changes the hash
- [x] Works on both Linux (sha256sum) and macOS (shasum)

---

## Phase 2: Dev Container Features

> **Note for Junior Engineers**: Phase 2 is more complex than Phase 1 because it involves downloading OCI artifacts from container registries. If you complete Phase 1 successfully, paude will already work with custom images - Phase 2 adds the convenience of dev container features but is not required for basic functionality. Consider completing Phases 1, 3, 4, and 5 first, then returning to Phase 2.

### Task 2.1: Feature manifest parsing

**Goal**: Parse the `features` object from devcontainer.json.

**Add to `lib/config.sh`**:

```bash
# Global variable for features
PAUDE_FEATURES=()  # Array of "url|options_json" strings

parse_features() {
    # Parse features from devcontainer.json into PAUDE_FEATURES array
    # Each element is "feature_url|options_json"

    PAUDE_FEATURES=()

    if [[ -z "$PAUDE_CONFIG_FILE" ]]; then
        return 0
    fi

    # Check if features exist
    if ! jq -e '.features' "$PAUDE_CONFIG_FILE" >/dev/null 2>&1; then
        return 0
    fi

    # Extract features as "url|options" pairs
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            PAUDE_FEATURES+=("$line")
        fi
    done < <(jq -r '.features | to_entries[] | "\(.key)|\(.value | @json)"' "$PAUDE_CONFIG_FILE")

    echo "Found ${#PAUDE_FEATURES[@]} feature(s)" >&2
}

has_features() {
    [[ ${#PAUDE_FEATURES[@]} -gt 0 ]]
}
```

**Example output**:
```
Input:
{
  "features": {
    "ghcr.io/devcontainers/features/python:1": {"version": "3.11"},
    "ghcr.io/devcontainers/features/go:1": {}
  }
}

PAUDE_FEATURES array:
[0] = "ghcr.io/devcontainers/features/python:1|{\"version\":\"3.11\"}"
[1] = "ghcr.io/devcontainers/features/go:1|{}"
```

**Acceptance Criteria Checklist**:
- [x] `parse_features` populates `PAUDE_FEATURES` array
- [x] Each element contains URL and options JSON
- [x] Empty features object results in empty array
- [x] Missing features property results in empty array

---

### Task 2.2: Feature download mechanism

**Goal**: Download dev container features from ghcr.io OCI registry.

**Create `lib/features.sh`**:

```bash
#!/bin/bash
# lib/features.sh - Dev container feature download and installation

FEATURE_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/paude/features"

download_feature() {
    # Downloads a feature from ghcr.io and extracts it
    # Args: feature_url (e.g., "ghcr.io/devcontainers/features/python:1")
    # Returns: path to extracted feature directory

    local feature_url="$1"
    local feature_hash
    feature_hash=$(echo "$feature_url" | sha256sum | cut -c1-12)
    local feature_dir="$FEATURE_CACHE_DIR/$feature_hash"

    # Check cache
    if [[ -d "$feature_dir" && -f "$feature_dir/install.sh" ]]; then
        echo "$feature_dir"
        return 0
    fi

    mkdir -p "$feature_dir"

    # Parse the feature URL
    # Format: ghcr.io/devcontainers/features/python:1
    local registry="${feature_url%%/*}"  # ghcr.io
    local path_and_tag="${feature_url#*/}"  # devcontainers/features/python:1
    local path="${path_and_tag%:*}"  # devcontainers/features/python
    local tag="${path_and_tag##*:}"  # 1

    echo "  → Downloading feature: $feature_url" >&2

    # Use ORAS or skopeo to download OCI artifact
    # ORAS is preferred for OCI artifacts
    if command -v oras >/dev/null 2>&1; then
        if ! oras pull "$feature_url" -o "$feature_dir" 2>&1; then
            echo "Error: Failed to download feature $feature_url" >&2
            rm -rf "$feature_dir"
            return 1
        fi
    elif command -v skopeo >/dev/null 2>&1; then
        # Alternative: use skopeo
        local tmp_tar="$feature_dir/feature.tar"
        if ! skopeo copy "docker://$feature_url" "oci-archive:$tmp_tar" 2>&1; then
            echo "Error: Failed to download feature $feature_url" >&2
            rm -rf "$feature_dir"
            return 1
        fi
        tar -xf "$tmp_tar" -C "$feature_dir"
        rm -f "$tmp_tar"
    else
        # Fallback: use curl with GitHub Container Registry API
        # This is a simplified approach that works for ghcr.io
        local manifest_url="https://$registry/v2/$path/manifests/$tag"
        local token_url="https://$registry/token?scope=repository:$path:pull"

        # Get anonymous token
        local token
        token=$(curl -s "$token_url" | jq -r '.token // empty')

        if [[ -z "$token" ]]; then
            echo "Error: Failed to get token for $feature_url" >&2
            rm -rf "$feature_dir"
            return 1
        fi

        # Get manifest
        local manifest
        manifest=$(curl -s -H "Authorization: Bearer $token" \
            -H "Accept: application/vnd.oci.image.manifest.v1+json" \
            "$manifest_url")

        # Get the layer digest (features are usually single-layer)
        local digest
        digest=$(echo "$manifest" | jq -r '.layers[0].digest // empty')

        if [[ -z "$digest" ]]; then
            echo "Error: Failed to get layer digest for $feature_url" >&2
            rm -rf "$feature_dir"
            return 1
        fi

        # Download and extract layer
        local blob_url="https://$registry/v2/$path/blobs/$digest"
        if ! curl -sL -H "Authorization: Bearer $token" "$blob_url" | tar -xz -C "$feature_dir"; then
            echo "Error: Failed to extract feature $feature_url" >&2
            rm -rf "$feature_dir"
            return 1
        fi
    fi

    # Verify install.sh exists
    if [[ ! -f "$feature_dir/install.sh" ]]; then
        echo "Error: Feature missing install.sh: $feature_url" >&2
        rm -rf "$feature_dir"
        return 1
    fi

    chmod +x "$feature_dir/install.sh"
    echo "$feature_dir"
}

clear_feature_cache() {
    rm -rf "$FEATURE_CACHE_DIR"
}
```

**Note**: This task requires either `oras`, `skopeo`, or network access to ghcr.io. The paude container already has `curl` and `jq`. The curl fallback should work for most cases.

**Manual Test** (run outside paude container, with network access):
```bash
# Source the libraries
source lib/config.sh
source lib/features.sh

# Test downloading a simple feature
feature_dir=$(download_feature "ghcr.io/devcontainers/features/common-utils:2")
echo "Downloaded to: $feature_dir"
ls -la "$feature_dir"
# Should show: devcontainer-feature.json, install.sh, and other files
```

**If the curl fallback doesn't work**: Install `oras` CLI tool:
```bash
# On macOS
brew install oras

# On Linux (download binary)
curl -LO https://github.com/oras-project/oras/releases/download/v1.1.0/oras_1.1.0_linux_amd64.tar.gz
tar -xzf oras_1.1.0_linux_amd64.tar.gz
sudo mv oras /usr/local/bin/
```

**Acceptance Criteria Checklist**:
- [x] `download_feature` downloads from ghcr.io
- [x] Feature is extracted to cache directory
- [x] `install.sh` exists in extracted directory
- [x] Cached features are reused
- [x] Error handling for failed downloads

---

### Task 2.3: Feature installation in Dockerfile

**Goal**: Generate Dockerfile RUN instructions to install features.

**Add to `lib/features.sh`**:

```bash
generate_feature_install_layer() {
    # Generate Dockerfile RUN instruction for a feature
    # Args: feature_dir, options_json
    # Output: writes Dockerfile snippet to stdout

    local feature_dir="$1"
    local options_json="$2"

    # Read feature metadata
    local feature_json="$feature_dir/devcontainer-feature.json"
    if [[ ! -f "$feature_json" ]]; then
        echo "# Warning: No devcontainer-feature.json in $feature_dir" >&2
        return 1
    fi

    local feature_id
    feature_id=$(jq -r '.id // "unknown"' "$feature_json")

    echo ""
    echo "# Feature: $feature_id"

    # Convert options JSON to environment variables
    # {"version": "3.11"} -> VERSION=3.11
    local env_vars=""
    if [[ -n "$options_json" && "$options_json" != "{}" ]]; then
        env_vars=$(echo "$options_json" | jq -r 'to_entries[] | "\(.key | ascii_upcase)=\(.value)"' | tr '\n' ' ')
    fi

    # Generate COPY and RUN
    echo "COPY --from=features $feature_dir /tmp/features/$feature_id"
    if [[ -n "$env_vars" ]]; then
        echo "RUN cd /tmp/features/$feature_id && $env_vars ./install.sh"
    else
        echo "RUN cd /tmp/features/$feature_id && ./install.sh"
    fi
}

generate_features_dockerfile() {
    # Generate complete Dockerfile section for all features
    # Reads from PAUDE_FEATURES array
    # Output: Dockerfile content to stdout

    if [[ ${#PAUDE_FEATURES[@]} -eq 0 ]]; then
        return 0
    fi

    echo ""
    echo "# === Dev Container Features ==="

    for feature_entry in "${PAUDE_FEATURES[@]}"; do
        local feature_url="${feature_entry%%|*}"
        local options_json="${feature_entry#*|}"

        local feature_dir
        feature_dir=$(download_feature "$feature_url")
        if [[ $? -ne 0 ]]; then
            echo "Error: Failed to download feature $feature_url" >&2
            return 1
        fi

        generate_feature_install_layer "$feature_dir" "$options_json"
    done

    echo ""
    echo "# Cleanup feature installers"
    echo "RUN rm -rf /tmp/features"
}
```

**Acceptance Criteria Checklist**:
- [x] Generates valid Dockerfile RUN instructions
- [x] Options are converted to environment variables
- [x] Feature install.sh is called correctly
- [x] Cleanup removes temporary feature files

---

### Task 2.4: Integrate features into build pipeline

**Goal**: Full end-to-end feature support in paude build.

**Modify `ensure_custom_image()`** to include features:

```bash
ensure_custom_image() {
    source "$SCRIPT_DIR/lib/hash.sh"
    source "$SCRIPT_DIR/lib/features.sh"

    local config_hash
    config_hash=$(compute_config_hash)
    local custom_image="paude-workspace:$config_hash"

    if podman image exists "$custom_image" 2>/dev/null && [[ "$FORCE_REBUILD" != "true" ]]; then
        echo "Using cached workspace image: $custom_image" >&2
        IMAGE_NAME="$custom_image"
        return 0
    fi

    echo "Building workspace image..." >&2
    prepare_build_dir
    trap cleanup_build_dir EXIT

    # Parse features
    parse_features

    # Generate Dockerfile
    local dockerfile_content=""

    if [[ -n "$PAUDE_BASE_IMAGE" ]]; then
        echo "  → Using base: $PAUDE_BASE_IMAGE" >&2
        dockerfile_content=$(generate_workspace_dockerfile "$PAUDE_BASE_IMAGE" "$PAUDE_BUILD_DIR")
    elif [[ -n "$PAUDE_DOCKERFILE" ]]; then
        # Build user image first, then extend (same as Task 1.4)
        if [[ ! -f "$PAUDE_DOCKERFILE" ]]; then
            echo "Error: Dockerfile not found: $PAUDE_DOCKERFILE" >&2
            exit 1
        fi

        local user_image="paude-user-base:$config_hash"
        local build_context="${PAUDE_BUILD_CONTEXT:-$(dirname "$PAUDE_DOCKERFILE")}"
        local build_args
        build_args=$(parse_build_args)

        echo "  → Building user Dockerfile..." >&2
        if ! podman build -t "$user_image" -f "$PAUDE_DOCKERFILE" $build_args "$build_context"; then
            echo "Error: Failed to build user Dockerfile" >&2
            exit 1
        fi

        dockerfile_content=$(generate_workspace_dockerfile "$user_image" "$PAUDE_BUILD_DIR")
    fi

    # Add feature installations before USER directive
    if has_features; then
        local features_block
        features_block=$(generate_features_dockerfile)

        # Insert features before "USER paude" line
        dockerfile_content=$(echo "$dockerfile_content" | sed '/^USER paude$/i\
'"$features_block"'
')
    fi

    echo "$dockerfile_content" > "$PAUDE_BUILD_DIR/Dockerfile"

    # Copy features to build context for COPY --from=features
    if has_features; then
        cp -r "$FEATURE_CACHE_DIR" "$PAUDE_BUILD_DIR/features"
    fi

    if ! podman build \
        --build-arg "BASE_IMAGE=${PAUDE_BASE_IMAGE:-$user_image}" \
        -t "$custom_image" \
        "$PAUDE_BUILD_DIR"; then
        echo "Error: Failed to build workspace image" >&2
        exit 1
    fi

    echo "Build complete (cached as $custom_image)" >&2
    IMAGE_NAME="$custom_image"
}
```

**Acceptance Criteria Checklist**:
- [x] Features are downloaded during build
- [x] Features are installed in the image
- [x] Feature options are passed correctly
- [x] Image caching still works with features
- [x] `--rebuild` forces re-download of features

---

## Phase 3: Lifecycle Commands

### Task 3.1: postCreateCommand support

**Goal**: Run setup commands after container start.

**Note**: postCreateCommand runs at **runtime**, not build time. It should run once when the workspace is first created, not on every `paude` invocation.

**Add to `lib/config.sh`**:
```bash
PAUDE_POST_CREATE_COMMAND=""

parse_post_create_command() {
    if [[ -z "$PAUDE_CONFIG_FILE" ]]; then
        return 0
    fi

    # postCreateCommand can be string or array
    local cmd
    cmd=$(jq -r 'if .postCreateCommand | type == "array" then .postCreateCommand | join(" && ") else .postCreateCommand // empty end' "$PAUDE_CONFIG_FILE")

    PAUDE_POST_CREATE_COMMAND="$cmd"
}
```

**Modify `run_claude()`** to run postCreateCommand on first run.

Find the final `podman run` command in `run_claude()`. Search for this pattern:
```bash
    podman run --rm -it \
        -w "$WORKSPACE_DIR" \
        "${network_args[@]}" \
```

This is the command that actually runs claude. Add the postCreateCommand logic **before** this final `podman run` command. Insert the following code:

```bash
    # === BEGIN: Add this block before the final podman run ===

    # Parse post-create command from config
    parse_post_create_command

    # Check if postCreateCommand needs to run
    local workspace_marker="$WORKSPACE_DIR/.paude-initialized"

    if [[ -n "$PAUDE_POST_CREATE_COMMAND" && ! -f "$workspace_marker" ]]; then
        echo "Running postCreateCommand: $PAUDE_POST_CREATE_COMMAND" >&2

        if ! podman run --rm \
            -w "$WORKSPACE_DIR" \
            "${network_args[@]}" \
            "${proxy_env[@]}" \
            "${ENV_ARGS[@]}" \
            "${MOUNT_ARGS[@]}" \
            "$IMAGE_NAME" \
            /bin/bash -c "$PAUDE_POST_CREATE_COMMAND"; then
            echo "Warning: postCreateCommand failed" >&2
        else
            # Mark as initialized only on success
            touch "$workspace_marker"
        fi
    fi

    # === END: postCreateCommand block ===

    # Now the existing podman run for claude continues below...
```

The final `podman run` for claude remains unchanged.

**Acceptance Criteria Checklist**:
- [x] postCreateCommand runs on first `paude` invocation
- [x] Does not run on subsequent invocations (marker file exists)
- [x] Supports both string and array formats
- [x] Failures show warning but don't block claude

---

### Task 3.2: containerEnv support

**Goal**: Pass environment variables from config to container.

**Add to `lib/config.sh`**:
```bash
PAUDE_CONTAINER_ENV=()

parse_container_env() {
    PAUDE_CONTAINER_ENV=()

    if [[ -z "$PAUDE_CONFIG_FILE" ]]; then
        return 0
    fi

    # Extract containerEnv as array of -e KEY=VALUE
    while IFS= read -r line; do
        if [[ -n "$line" ]]; then
            PAUDE_CONTAINER_ENV+=("-e" "$line")
        fi
    done < <(jq -r '.containerEnv // {} | to_entries[] | "\(.key)=\(.value)"' "$PAUDE_CONFIG_FILE")
}
```

**Modify `run_claude()`**:

1. Add `parse_container_env` call inside `run_claude()`, after the network/proxy setup. Find this code block:

```bash
    if [[ "$ALLOW_NETWORK" == "true" ]]; then
        :  # No proxy setup needed
    else
        network_args=(--network "$INTERNAL_NETWORK")
        ...
    fi
```

Add `parse_container_env` right after this if/else block:

```bash
    # Add this line after the network_args/proxy_env setup:
    parse_container_env
```

2. Find the final `podman run` command and add `"${PAUDE_CONTAINER_ENV[@]}"` to the arguments. The updated command should look like:

```bash
    podman run --rm -it \
        -w "$WORKSPACE_DIR" \
        "${network_args[@]}" \
        "${proxy_env[@]}" \
        "${ENV_ARGS[@]}" \
        "${PAUDE_CONTAINER_ENV[@]}" \
        "${MOUNT_ARGS[@]}" \
        "$IMAGE_NAME" \
        "${CLAUDE_ARGS[@]}"
```

**Note**: `${PAUDE_CONTAINER_ENV[@]}` goes AFTER `${ENV_ARGS[@]}` so that paude's required vars take precedence if there's a conflict.

**Acceptance Criteria Checklist**:
- [x] containerEnv variables are available inside container
- [x] paude's required env vars (CLAUDE_CODE_USE_VERTEX, etc.) take precedence
- [x] Empty containerEnv doesn't cause errors

---

## Phase 4: paude.json Native Format

### Task 4.1: paude.json parser

**Goal**: Support simpler paude-native config format.

**Note**: This task extends work from Task 1.1. You will **replace** the simple `_parse_paude_json` function created earlier with this extended version.

In `lib/config.sh`, add these global variable declarations at the top (near the other globals), then replace the `_parse_paude_json` function:

```bash
PAUDE_PACKAGES=()
PAUDE_SETUP_COMMAND=""

_parse_paude_json() {
    # paude.json format:
    # {
    #   "base": "python:3.11-slim",
    #   "packages": ["git", "make", "gcc"],
    #   "setup": "pip install -r requirements.txt"
    # }

    PAUDE_BASE_IMAGE=$(jq -r '.base // empty' "$PAUDE_CONFIG_FILE")
    PAUDE_DOCKERFILE=""
    PAUDE_BUILD_CONTEXT=""

    # Parse packages array
    PAUDE_PACKAGES=()
    while IFS= read -r pkg; do
        if [[ -n "$pkg" ]]; then
            PAUDE_PACKAGES+=("$pkg")
        fi
    done < <(jq -r '.packages[]? // empty' "$PAUDE_CONFIG_FILE")

    # Parse setup command (maps to postCreateCommand)
    PAUDE_SETUP_COMMAND=$(jq -r '.setup // empty' "$PAUDE_CONFIG_FILE")
    if [[ -n "$PAUDE_SETUP_COMMAND" ]]; then
        PAUDE_POST_CREATE_COMMAND="$PAUDE_SETUP_COMMAND"
    fi
}
```

**Modify `generate_workspace_dockerfile()`** in `lib/config.sh` to include packages.

Replace the entire `generate_workspace_dockerfile` function with this updated version that handles packages:

```bash
generate_workspace_dockerfile() {
    # Generates a Dockerfile that wraps the user's base image with paude requirements
    # Output: writes Dockerfile content to stdout
    local base_image="$1"

    cat <<'DOCKERFILE_HEADER'
# Auto-generated by paude - DO NOT EDIT
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
DOCKERFILE_HEADER

    # Add user-specified packages if any (from paude.json "packages" array)
    if [[ ${#PAUDE_PACKAGES[@]} -gt 0 ]]; then
        local pkg_list="${PAUDE_PACKAGES[*]}"
        cat <<PACKAGES

# User-specified packages from paude.json
RUN if command -v apt-get >/dev/null 2>&1; then \\
        apt-get update && apt-get install -y $pkg_list && rm -rf /var/lib/apt/lists/*; \\
    elif command -v apk >/dev/null 2>&1; then \\
        apk add --no-cache $pkg_list; \\
    elif command -v yum >/dev/null 2>&1; then \\
        yum install -y $pkg_list && yum clean all; \\
    fi
PACKAGES
    fi

    # Standard paude requirements
    cat <<'DOCKERFILE_BODY'

# Install required system packages
RUN if command -v apt-get >/dev/null 2>&1; then \
        apt-get update && \
        apt-get install -y --no-install-recommends git curl ca-certificates && \
        rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
        apk add --no-cache git curl ca-certificates; \
    elif command -v yum >/dev/null 2>&1; then \
        yum install -y git curl ca-certificates && \
        yum clean all; \
    else \
        echo "Warning: Unknown package manager, git may not be available" >&2; \
    fi

# Install Node.js if not present (required for claude-code)
RUN if ! command -v node >/dev/null 2>&1; then \
        if command -v apt-get >/dev/null 2>&1; then \
            curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
            apt-get install -y nodejs && \
            rm -rf /var/lib/apt/lists/*; \
        elif command -v apk >/dev/null 2>&1; then \
            apk add --no-cache nodejs npm; \
        else \
            echo "Error: Cannot install Node.js - unsupported base image" >&2 && exit 1; \
        fi \
    fi

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Create paude user if it doesn't exist
RUN id paude >/dev/null 2>&1 || useradd -m -s /bin/bash paude 2>/dev/null || adduser -D -s /bin/bash paude

# Copy entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
DOCKERFILE_BODY
}
```

**Acceptance Criteria Checklist**:
- [x] `paude.json` with `base` works like `image` in devcontainer.json
- [x] `packages` array installs specified packages
- [x] `setup` maps to postCreateCommand behavior
- [x] Simpler format documented in README

---

## Phase 5: Testing and Documentation

### Task 5.1: Unit tests for config parsing

Already covered in Task 1.1. Ensure all tests pass.

### Task 5.2: Integration tests with sample projects

**Create `test/fixtures/` with sample projects**:

```bash
# Python project
mkdir -p test/fixtures/python-project/.devcontainer
cat > test/fixtures/python-project/.devcontainer/devcontainer.json << 'EOF'
{
    "image": "python:3.11-slim",
    "postCreateCommand": "pip install --user pytest"
}
EOF
echo "print('Hello from Python')" > test/fixtures/python-project/hello.py

# Node project
mkdir -p test/fixtures/node-project/.devcontainer
cat > test/fixtures/node-project/.devcontainer/devcontainer.json << 'EOF'
{
    "image": "node:20-slim"
}
EOF
echo "console.log('Hello from Node')" > test/fixtures/node-project/hello.js

# Go project
mkdir -p test/fixtures/go-project/.devcontainer
cat > test/fixtures/go-project/.devcontainer/devcontainer.json << 'EOF'
{
    "image": "golang:1.21"
}
EOF
```

**Create `test/test_integration.sh`**:
```bash
#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAUDE="$SCRIPT_DIR/../paude"

echo "Running integration tests..."

test_python_project() {
    echo "Testing Python project..."
    cd "$SCRIPT_DIR/fixtures/python-project"
    PAUDE_DEV=1 "$PAUDE" -- -p "Run: python hello.py" --max-turns 1
}

test_node_project() {
    echo "Testing Node project..."
    cd "$SCRIPT_DIR/fixtures/node-project"
    PAUDE_DEV=1 "$PAUDE" -- -p "Run: node hello.js" --max-turns 1
}

test_python_project
test_node_project

echo "All integration tests passed!"
```

**Acceptance Criteria Checklist**:
- [x] `test/fixtures/python-project/` exists with devcontainer.json
- [x] `test/fixtures/node-project/` exists with devcontainer.json
- [x] `test/fixtures/go-project/` exists with devcontainer.json
- [x] `bash test/test_integration.sh` runs without errors

### Task 5.3: Update documentation

**Add to README.md**:

```markdown
## Custom Container Environments (BYOC)

paude supports custom container configurations via devcontainer.json or paude.json.

### Using devcontainer.json

Create `.devcontainer/devcontainer.json`:

```json
{
    "image": "python:3.11-slim",
    "postCreateCommand": "pip install -r requirements.txt"
}
```

### Using paude.json (simpler)

Create `paude.json` at project root:

```json
{
    "base": "python:3.11-slim",
    "packages": ["make", "gcc"],
    "setup": "pip install -r requirements.txt"
}
```

### Supported Properties

| Property | Description |
|----------|-------------|
| `image` | Base container image |
| `build.dockerfile` | Path to custom Dockerfile |
| `build.context` | Build context directory |
| `features` | Dev container features |
| `postCreateCommand` | Run after first start |
| `containerEnv` | Environment variables |

### Unsupported Properties (Security)

These properties are ignored for security:
- `mounts` - paude controls mounts
- `runArgs` - paude controls run arguments
- `privileged` - never allowed
```

**Also update `show_help()` in `paude`** to mention the `--rebuild` flag (if not already done in Task 1.2).

**Acceptance Criteria Checklist**:
- [x] README.md has "Custom Container Environments (BYOC)" section
- [x] Examples for both devcontainer.json and paude.json included
- [x] Supported properties table is present
- [x] Unsupported properties are documented with security reasons

### Task 5.4: Create example configs

Already covered in Task 5.2 fixtures. Copy to `examples/`:

```bash
mkdir -p examples
cp -r test/fixtures/python-project examples/python
cp -r test/fixtures/node-project examples/node
cp -r test/fixtures/go-project examples/go
```

**Acceptance Criteria Checklist**:
- [x] `examples/python/` exists with working devcontainer.json
- [x] `examples/node/` exists with working devcontainer.json
- [x] `examples/go/` exists with working devcontainer.json

---

## Summary: Implementation Checklist

### Phase 1 (MVP)
- [x] 1.1: lib/config.sh with detect_config, parse_config
- [x] 1.2: paude script integration, --rebuild flag
- [x] 1.3: Simple image support with generated Dockerfile
- [x] 1.4: Dockerfile build support
- [x] 1.5: Config hash caching

### Phase 2 (Features)
- [x] 2.1: parse_features function
- [x] 2.2: download_feature function
- [x] 2.3: generate_feature_install_layer function
- [x] 2.4: Full integration

### Phase 3 (Lifecycle)
- [x] 3.1: postCreateCommand support
- [x] 3.2: containerEnv support

### Phase 4 (Convenience)
- [x] 4.1: paude.json parser

### Phase 5 (Polish)
- [x] 5.1: Unit tests passing
- [x] 5.2: Integration tests
- [x] 5.3: README updated
- [x] 5.4: Example configs

---

## Troubleshooting Guide

### Build fails with "permission denied"
- Ensure entrypoint.sh is copied with executable permissions
- Check that COPY uses `--chmod=755`

### Features fail to download
- Check network connectivity
- Verify feature URL format: `ghcr.io/owner/repo/feature:tag`
- Try clearing cache: `rm -rf ~/.cache/paude/features`

### Image not using latest config
- Run `paude --rebuild` to force rebuild
- Check that config hash is computed correctly

### postCreateCommand not running
- Check if `.paude-initialized` marker exists
- Remove marker to re-run: `rm .paude-initialized`

---

## Appendix A: File Structure After Implementation

After completing all phases, your directory structure should look like:

```
paude/
├── paude                      # Modified - added BYOC support
├── lib/
│   ├── config.sh             # Created in Task 1.1, extended in 2.1, 3.1, 3.2, 4.1
│   ├── hash.sh               # Created in Task 1.5
│   └── features.sh           # Created in Task 2.2, extended in 2.3
├── containers/
│   ├── paude/
│   │   ├── Dockerfile        # Existing (unchanged)
│   │   └── entrypoint.sh     # Existing (unchanged)
│   └── proxy/                # Existing (unchanged)
├── test/
│   ├── test_paude.sh         # Existing
│   ├── test_config.sh        # Created in Task 1.1
│   ├── test_hash.sh          # Created in Task 1.5
│   ├── test_integration.sh   # Created in Task 5.2
│   └── fixtures/
│       ├── python-project/   # Created in Task 5.2
│       ├── node-project/     # Created in Task 5.2
│       └── go-project/       # Created in Task 5.2
├── examples/
│   ├── python/               # Created in Task 5.4
│   ├── node/                 # Created in Task 5.4
│   └── go/                   # Created in Task 5.4
└── README.md                 # Modified in Task 5.3
```

---

## Appendix B: lib/config.sh Final State

After completing all tasks, `lib/config.sh` should contain these functions (in order):

```bash
#!/bin/bash
# lib/config.sh - Configuration detection and parsing for BYOC

# === Global Variables ===
# Set by detect_config/parse_config:
PAUDE_CONFIG_FILE=""
PAUDE_CONFIG_TYPE="default"
PAUDE_BASE_IMAGE=""
PAUDE_DOCKERFILE=""
PAUDE_BUILD_CONTEXT=""

# Set by parse_features (Task 2.1):
PAUDE_FEATURES=()

# Set by parse_post_create_command (Task 3.1):
PAUDE_POST_CREATE_COMMAND=""

# Set by parse_container_env (Task 3.2):
PAUDE_CONTAINER_ENV=()

# Set by _parse_paude_json (Task 4.1):
PAUDE_PACKAGES=()
PAUDE_SETUP_COMMAND=""

# === Functions ===

detect_config() { ... }           # Task 1.1
parse_config() { ... }            # Task 1.1
_parse_devcontainer() { ... }     # Task 1.1
_parse_paude_json() { ... }       # Task 1.1, replaced in Task 4.1
_warn_unsupported_properties() { ... }  # Task 1.1
has_custom_config() { ... }       # Task 1.1
needs_custom_build() { ... }      # Task 1.1
generate_workspace_dockerfile() { ... } # Task 1.3, replaced in Task 4.1
parse_build_args() { ... }        # Task 1.4
parse_features() { ... }          # Task 2.1
has_features() { ... }            # Task 2.1
parse_post_create_command() { ... }     # Task 3.1
parse_container_env() { ... }     # Task 3.2
```

**Note**: The `{ ... }` indicates the function body from the respective task. Copy each function's complete implementation from its task.

---

## Appendix C: Source Order in paude Script

After Task 1.2, the top of the `paude` script should include these source statements (after `SCRIPT_DIR` is set):

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Export script dir for library modules (they need to find containers/paude/entrypoint.sh)
PAUDE_SCRIPT_DIR="$SCRIPT_DIR"

# Source library modules
source "$SCRIPT_DIR/lib/config.sh"
# Note: hash.sh and features.sh are sourced inside ensure_custom_image()
# only when needed, not at the top level
```

The main execution flow at the bottom should be:

```bash
# Main execution
check_requirements

# Setup workspace dir early (needed for config detection)
WORKSPACE_DIR="$(pwd -P)"

# Detect and parse configuration
detect_config "$WORKSPACE_DIR"
if has_custom_config; then
    parse_config || exit 1
fi

ensure_images
setup_environment
setup_mounts
check_macos_volumes
check_git_safety

if [[ "$ALLOW_NETWORK" == "false" ]]; then
    setup_proxy
fi

run_claude
```
