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
