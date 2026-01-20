#!/bin/bash
# Tests for volume mount setup logic
# Tests the mount path generation without actually mounting

source "$SCRIPT_DIR/test_helpers.sh"

# Test basic workspace mount
test_workspace_mount() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    local mount_args
    mount_args=$(
        cd "$test_workspace"
        WORKSPACE_DIR="$(pwd -P)"
        MOUNT_ARGS=(
            -v "$WORKSPACE_DIR:$WORKSPACE_DIR:rw"
        )
        echo "${MOUNT_ARGS[*]}"
    )

    assert_contains "$mount_args" "$test_workspace:$test_workspace:rw" "workspace mounted at same path with rw"

    cleanup_test_workspace "$test_workspace"
}

# Test gcloud mount logic
test_gcloud_mount_exists() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    mkdir -p "$test_workspace/.config/gcloud"
    touch "$test_workspace/.config/gcloud/credentials"

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        GCLOUD_DIR="$(resolve_path "$HOME/.config/gcloud")"
        if [[ -n "$GCLOUD_DIR" && -d "$GCLOUD_DIR" ]]; then
            MOUNT_ARGS+=(-v "$GCLOUD_DIR:/home/paude/.config/gcloud:ro")
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    assert_contains "$mount_args" "/home/paude/.config/gcloud:ro" "gcloud dir mounted read-only"
    assert_contains "$mount_args" "$test_workspace/.config/gcloud" "gcloud mount uses resolved path"

    cleanup_test_workspace "$test_workspace"
}

test_gcloud_mount_missing() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        GCLOUD_DIR="$(resolve_path "$HOME/.config/gcloud")"
        if [[ -n "$GCLOUD_DIR" && -d "$GCLOUD_DIR" ]]; then
            MOUNT_ARGS+=(-v "$GCLOUD_DIR:/home/paude/.config/gcloud:ro")
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    assert_not_contains "$mount_args" "gcloud" "no gcloud mount when dir missing"

    cleanup_test_workspace "$test_workspace"
}

# Test claude config mount
test_claude_dir_mount() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    mkdir -p "$test_workspace/.claude"
    touch "$test_workspace/.claude/settings.json"

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        if [[ -d "$HOME/.claude" ]]; then
            CLAUDE_DIR="$(resolve_path "$HOME/.claude")"
            if [[ -n "$CLAUDE_DIR" && -d "$CLAUDE_DIR" ]]; then
                MOUNT_ARGS+=(-v "$CLAUDE_DIR:/tmp/claude.seed:ro")
            fi
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    assert_contains "$mount_args" "/tmp/claude.seed:ro" "claude dir mounted as seed"

    cleanup_test_workspace "$test_workspace"
}

# Test plugins mount at original path
test_plugins_mount() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    mkdir -p "$test_workspace/.claude/plugins"
    touch "$test_workspace/.claude/plugins/myplugin"

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        if [[ -d "$HOME/.claude" ]]; then
            CLAUDE_DIR="$(resolve_path "$HOME/.claude")"
            if [[ -n "$CLAUDE_DIR" && -d "$CLAUDE_DIR" ]]; then
                MOUNT_ARGS+=(-v "$CLAUDE_DIR:/tmp/claude.seed:ro")
                if [[ -d "$CLAUDE_DIR/plugins" ]]; then
                    MOUNT_ARGS+=(-v "$CLAUDE_DIR/plugins:$CLAUDE_DIR/plugins:ro")
                fi
            fi
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    # Plugins should be mounted at original host path
    assert_contains "$mount_args" "plugins:$test_workspace/.claude/plugins:ro" "plugins mounted at original path"

    cleanup_test_workspace "$test_workspace"
}

# Test gitconfig mount
test_gitconfig_mount() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    touch "$test_workspace/.gitconfig"

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        GITCONFIG="$(resolve_path "$HOME/.gitconfig")"
        if [[ -n "$GITCONFIG" && -f "$GITCONFIG" ]]; then
            MOUNT_ARGS+=(-v "$GITCONFIG:/home/paude/.gitconfig:ro")
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    assert_contains "$mount_args" "/home/paude/.gitconfig:ro" "gitconfig mounted read-only"

    cleanup_test_workspace "$test_workspace"
}

# Test claude.json mount
test_claude_json_mount() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    touch "$test_workspace/.claude.json"

    local mount_args
    mount_args=$(
        HOME="$test_workspace"
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        MOUNT_ARGS=()
        if [[ -f "$HOME/.claude.json" ]]; then
            CLAUDE_JSON="$(resolve_path "$HOME/.claude.json")"
            if [[ -n "$CLAUDE_JSON" && -f "$CLAUDE_JSON" ]]; then
                MOUNT_ARGS+=(-v "$CLAUDE_JSON:/tmp/claude.json.seed:ro")
            fi
        fi
        echo "${MOUNT_ARGS[*]}"
    )

    assert_contains "$mount_args" "/tmp/claude.json.seed:ro" "claude.json mounted as seed"

    cleanup_test_workspace "$test_workspace"
}

# Run all tests
test_workspace_mount
test_gcloud_mount_exists
test_gcloud_mount_missing
test_claude_dir_mount
test_plugins_mount
test_gitconfig_mount
test_claude_json_mount
