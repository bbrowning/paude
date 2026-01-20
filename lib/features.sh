#!/bin/bash
# lib/features.sh - Dev container feature download and installation

FEATURE_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/paude/features"

download_feature() {
    # Downloads a feature from ghcr.io and extracts it
    # Args: feature_url (e.g., "ghcr.io/devcontainers/features/python:1")
    # Returns: path to extracted feature directory

    local feature_url="$1"
    local feature_hash
    if command -v sha256sum >/dev/null 2>&1; then
        feature_hash=$(echo "$feature_url" | sha256sum | cut -c1-12)
    elif command -v shasum >/dev/null 2>&1; then
        feature_hash=$(echo "$feature_url" | shasum -a 256 | cut -c1-12)
    else
        feature_hash=$(echo "$feature_url" | md5sum | cut -c1-12)
    fi
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
