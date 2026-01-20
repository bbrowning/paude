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
