#!/bin/bash
# Test helpers for paude tests
# Provides mock commands and utilities

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Track mock calls
MOCK_PODMAN_CALLS=()
MOCK_GIT_CALLS=()

# Mock podman command - records calls but doesn't execute
mock_podman() {
    MOCK_PODMAN_CALLS+=("$*")
    case "$1" in
        image)
            if [[ "$2" == "exists" ]]; then
                # Simulate image exists
                return 0
            fi
            ;;
        network)
            if [[ "$2" == "exists" ]]; then
                return 0
            fi
            ;;
        run)
            # Don't actually run
            return 0
            ;;
        *)
            return 0
            ;;
    esac
}

# Mock git command
mock_git() {
    MOCK_GIT_CALLS+=("$*")
    case "$1" in
        -C)
            # git -C <path> remote
            if [[ "$3" == "remote" ]]; then
                echo "origin"
            fi
            ;;
        *)
            return 0
            ;;
    esac
}

# Source paude script in a way that allows testing functions
# without executing main logic
source_paude_functions() {
    # Create a subshell-safe way to get just the functions
    # We'll extract and source just the function definitions

    # For now, we test by running the script with specific args
    # and checking output
    :
}

# Reset mock state
reset_mocks() {
    MOCK_PODMAN_CALLS=()
    MOCK_GIT_CALLS=()
}

# Assert that output contains expected string
assert_contains() {
    local output="$1"
    local expected="$2"
    local test_name="$3"

    if [[ "$output" == *"$expected"* ]]; then
        pass "$test_name"
    else
        fail "$test_name" "Expected output to contain: $expected"
    fi
}

# Assert that output does not contain string
assert_not_contains() {
    local output="$1"
    local unexpected="$2"
    local test_name="$3"

    if [[ "$output" != *"$unexpected"* ]]; then
        pass "$test_name"
    else
        fail "$test_name" "Expected output to NOT contain: $unexpected"
    fi
}

# Assert exit code
assert_exit_code() {
    local actual="$1"
    local expected="$2"
    local test_name="$3"

    if [[ "$actual" -eq "$expected" ]]; then
        pass "$test_name"
    else
        fail "$test_name" "Expected exit code $expected, got $actual"
    fi
}

# Assert strings are equal
assert_equals() {
    local actual="$1"
    local expected="$2"
    local test_name="$3"

    if [[ "$actual" == "$expected" ]]; then
        pass "$test_name"
    else
        fail "$test_name" "Expected '$expected', got '$actual'"
    fi
}

# Create a temporary directory for test isolation
# Returns the resolved (physical) path to handle macOS symlinks
create_test_workspace() {
    local tmpdir
    tmpdir=$(mktemp -d)
    # Resolve symlinks (e.g., /var/folders -> /private/var/folders on macOS)
    cd -P "$tmpdir" && pwd -P
}

# Cleanup test workspace
cleanup_test_workspace() {
    local tmpdir="$1"
    # Handle both Linux (/tmp/*) and macOS (/private/var/folders/*) temp paths
    if [[ -d "$tmpdir" && ( "$tmpdir" == /tmp/* || "$tmpdir" == /private/var/folders/* ) ]]; then
        rm -rf "$tmpdir"
    fi
}
