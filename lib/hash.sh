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
