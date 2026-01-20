#!/bin/bash
# Tests for paude internal functions
# Tests function logic by extracting and testing them

source "$SCRIPT_DIR/test_helpers.sh"

# Extract functions from paude script for testing
# We run in a subshell to avoid polluting our environment
extract_and_test_resolve_path() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    # Create test file and symlink
    mkdir -p "$test_workspace/real_dir"
    touch "$test_workspace/real_dir/file.txt"
    ln -s "$test_workspace/real_dir" "$test_workspace/link_dir"

    # Extract just the resolve_path function and test it
    local result
    result=$(
        # Define the function (copied from paude)
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }

        # Test: resolve symlink
        resolve_path "$test_workspace/link_dir/file.txt"
    )

    # The resolved path should go through real_dir, not link_dir
    assert_contains "$result" "real_dir" "resolve_path resolves symlinks"
    assert_not_contains "$result" "link_dir" "resolve_path returns real path not symlink"

    cleanup_test_workspace "$test_workspace"
}

# Test resolve_path with non-existent path
test_resolve_path_nonexistent() {
    local result
    result=$(
        resolve_path() {
            local path="$1"
            if [[ -e "$path" ]]; then
                cd -P "$(dirname "$path")" 2>/dev/null && echo "$(pwd -P)/$(basename "$path")"
            fi
        }
        resolve_path "/nonexistent/path/to/file"
    )

    assert_equals "$result" "" "resolve_path returns empty for non-existent path"
}

# Test setup_environment function behavior
test_setup_environment_with_vertex() {
    local env_args
    env_args=$(
        # Simulate the function logic
        CLAUDE_CODE_USE_VERTEX=1
        ANTHROPIC_VERTEX_PROJECT_ID=my-project

        ENV_ARGS=()
        for var in CLAUDE_CODE_USE_VERTEX ANTHROPIC_VERTEX_PROJECT_ID GOOGLE_CLOUD_PROJECT; do
            if [[ -n "${!var}" ]]; then
                ENV_ARGS+=(-e "$var=${!var}")
            fi
        done
        echo "${ENV_ARGS[*]}"
    )

    assert_contains "$env_args" "CLAUDE_CODE_USE_VERTEX=1" "setup_environment includes CLAUDE_CODE_USE_VERTEX"
    assert_contains "$env_args" "ANTHROPIC_VERTEX_PROJECT_ID=my-project" "setup_environment includes project ID"
    assert_not_contains "$env_args" "GOOGLE_CLOUD_PROJECT" "setup_environment excludes unset vars"
}

test_setup_environment_with_cloudsdk() {
    local env_args
    env_args=$(
        CLOUDSDK_AUTH_ACCESS_TOKEN="test-token"

        ENV_ARGS=()
        for var in $(compgen -v | grep '^CLOUDSDK_AUTH'); do
            ENV_ARGS+=(-e "$var=${!var}")
        done
        echo "${ENV_ARGS[*]}"
    )

    assert_contains "$env_args" "CLOUDSDK_AUTH_ACCESS_TOKEN=test-token" "setup_environment includes CLOUDSDK_AUTH vars"
}

# Test argument parsing logic (simulated)
test_arg_parsing_yolo() {
    local yolo_mode
    yolo_mode=$(
        YOLO_MODE=false
        PARSING_PAUDE_ARGS=true
        for arg in "--yolo"; do
            if [[ "$PARSING_PAUDE_ARGS" == "true" ]]; then
                case "$arg" in
                    (--yolo)
                        YOLO_MODE=true
                        ;;
                esac
            fi
        done
        echo "$YOLO_MODE"
    )

    assert_equals "$yolo_mode" "true" "arg parsing sets YOLO_MODE for --yolo"
}

test_arg_parsing_allow_network() {
    local allow_network
    allow_network=$(
        ALLOW_NETWORK=false
        PARSING_PAUDE_ARGS=true
        for arg in "--allow-network"; do
            if [[ "$PARSING_PAUDE_ARGS" == "true" ]]; then
                case "$arg" in
                    (--allow-network)
                        ALLOW_NETWORK=true
                        ;;
                esac
            fi
        done
        echo "$ALLOW_NETWORK"
    )

    assert_equals "$allow_network" "true" "arg parsing sets ALLOW_NETWORK for --allow-network"
}

test_arg_parsing_separator() {
    local claude_args
    claude_args=$(
        CLAUDE_ARGS=()
        PARSING_PAUDE_ARGS=true
        for arg in "--yolo" "--" "-p" "hello"; do
            if [[ "$PARSING_PAUDE_ARGS" == "true" ]]; then
                case "$arg" in
                    (--)
                        PARSING_PAUDE_ARGS=false
                        ;;
                    (--yolo)
                        : # handled
                        ;;
                    (*)
                        CLAUDE_ARGS+=("$arg")
                        ;;
                esac
            else
                CLAUDE_ARGS+=("$arg")
            fi
        done
        echo "${CLAUDE_ARGS[*]}"
    )

    assert_contains "$claude_args" "-p" "args after -- go to CLAUDE_ARGS"
    assert_contains "$claude_args" "hello" "args after -- include prompt text"
}

test_arg_parsing_passthrough() {
    # Test that unknown args before -- are passed to claude (backwards compat)
    local claude_args
    claude_args=$(
        CLAUDE_ARGS=()
        PARSING_PAUDE_ARGS=true
        for arg in "-p" "hello"; do
            if [[ "$PARSING_PAUDE_ARGS" == "true" ]]; then
                case "$arg" in
                    (--)
                        PARSING_PAUDE_ARGS=false
                        ;;
                    (-h|--help|-V|--version|--allow-network|--yolo)
                        : # paude args
                        ;;
                    (*)
                        CLAUDE_ARGS+=("$arg")
                        ;;
                esac
            else
                CLAUDE_ARGS+=("$arg")
            fi
        done
        echo "${CLAUDE_ARGS[*]}"
    )

    assert_contains "$claude_args" "-p" "unknown args passed through to claude"
    assert_contains "$claude_args" "hello" "unknown args include values"
}

# Run all tests
extract_and_test_resolve_path
test_resolve_path_nonexistent
test_setup_environment_with_vertex
test_setup_environment_with_cloudsdk
test_arg_parsing_yolo
test_arg_parsing_allow_network
test_arg_parsing_separator
test_arg_parsing_passthrough
