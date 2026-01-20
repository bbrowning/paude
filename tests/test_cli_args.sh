#!/bin/bash
# Tests for CLI argument parsing
# These tests verify flag parsing without running containers

source "$SCRIPT_DIR/test_helpers.sh"

# Test --help flag
test_help_short_flag() {
    local output
    output=$("$PROJECT_ROOT/paude" -h 2>&1)
    local exit_code=$?

    assert_exit_code "$exit_code" 0 "-h exits with code 0"
    assert_contains "$output" "paude - Run Claude Code" "-h shows description"
    assert_contains "$output" "USAGE:" "-h shows usage section"
    assert_contains "$output" "--yolo" "-h shows --yolo option"
    assert_contains "$output" "--allow-network" "-h shows --allow-network option"
    assert_contains "$output" "EXAMPLES:" "-h shows examples"
    assert_contains "$output" "SECURITY:" "-h shows security section"
}

test_help_long_flag() {
    local output
    output=$("$PROJECT_ROOT/paude" --help 2>&1)
    local exit_code=$?

    assert_exit_code "$exit_code" 0 "--help exits with code 0"
    assert_contains "$output" "paude - Run Claude Code" "--help shows description"
}

# Test --version flag
test_version_short_flag() {
    local output
    output=$("$PROJECT_ROOT/paude" -V 2>&1)
    local exit_code=$?

    assert_exit_code "$exit_code" 0 "-V exits with code 0"
    assert_contains "$output" "paude" "-V shows paude name"
    # Version should be a semver-like pattern
    if [[ "$output" =~ [0-9]+\.[0-9]+\.[0-9]+ ]]; then
        pass "-V shows version number"
    else
        fail "-V shows version number" "No version pattern found in: $output"
    fi
}

test_version_long_flag() {
    local output
    output=$("$PROJECT_ROOT/paude" --version 2>&1)
    local exit_code=$?

    assert_exit_code "$exit_code" 0 "--version exits with code 0"
    assert_contains "$output" "paude" "--version shows paude name"
}

# Test dev mode indicator in version
test_version_dev_mode() {
    local output
    output=$(PAUDE_DEV=1 "$PROJECT_ROOT/paude" --version 2>&1)

    assert_contains "$output" "development" "--version shows dev mode when PAUDE_DEV=1"
    assert_contains "$output" "building locally" "--version indicates local builds"
}

test_version_installed_mode() {
    local output
    output=$(PAUDE_DEV=0 "$PROJECT_ROOT/paude" --version 2>&1)

    assert_contains "$output" "installed" "--version shows installed mode when PAUDE_DEV=0"
}

# Test custom registry in version output
test_version_custom_registry() {
    local output
    output=$(PAUDE_REGISTRY="ghcr.io/test" "$PROJECT_ROOT/paude" --version 2>&1)

    assert_contains "$output" "ghcr.io/test" "--version shows custom registry"
}

# Run all tests
test_help_short_flag
test_help_long_flag
test_version_short_flag
test_version_long_flag
test_version_dev_mode
test_version_installed_mode
test_version_custom_registry
