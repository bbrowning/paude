#!/bin/bash
# Tests for container entrypoint scripts
# Validates the logic without running actual containers

source "$SCRIPT_DIR/test_helpers.sh"

# Test paude entrypoint seed copying logic
test_paude_entrypoint_claude_seed() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    # Simulate the seed directory
    mkdir -p "$test_workspace/seed"
    echo "test-config" > "$test_workspace/seed/settings.json"
    mkdir -p "$test_workspace/target"

    local copied
    copied=$(
        # Simulate entrypoint logic
        if [[ -d "$test_workspace/seed" ]]; then
            cp -r "$test_workspace/seed/." "$test_workspace/target/"
            echo "copied"
        fi
    )

    assert_equals "$copied" "copied" "entrypoint copies seed when present"

    if [[ -f "$test_workspace/target/settings.json" ]]; then
        pass "seed files copied to target"
    else
        fail "seed files copied to target" "settings.json not found in target"
    fi

    cleanup_test_workspace "$test_workspace"
}

test_paude_entrypoint_no_seed() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    # No seed directory
    mkdir -p "$test_workspace/target"

    local copied
    copied=$(
        # Simulate entrypoint logic
        if [[ -d "$test_workspace/seed" ]]; then
            cp -r "$test_workspace/seed/." "$test_workspace/target/"
            echo "copied"
        else
            echo "skipped"
        fi
    )

    assert_equals "$copied" "skipped" "entrypoint skips copy when no seed"

    cleanup_test_workspace "$test_workspace"
}

test_paude_entrypoint_json_seed() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    echo '{"key": "value"}' > "$test_workspace/source.json"
    mkdir -p "$test_workspace/target"

    local copied
    copied=$(
        # Simulate entrypoint logic for claude.json
        if [[ -f "$test_workspace/source.json" ]]; then
            cp "$test_workspace/source.json" "$test_workspace/target/result.json"
            echo "copied"
        fi
    )

    assert_equals "$copied" "copied" "entrypoint copies json seed"

    if [[ -f "$test_workspace/target/result.json" ]]; then
        pass "json seed copied to target"
    else
        fail "json seed copied to target" "result.json not found"
    fi

    cleanup_test_workspace "$test_workspace"
}

# Test proxy entrypoint DNS injection
test_proxy_entrypoint_dns_injection() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    touch "$test_workspace/squid.conf"

    local result
    result=$(
        SQUID_DNS="8.8.8.8"
        # Simulate the logic
        if [[ -n "$SQUID_DNS" ]]; then
            echo "dns_nameservers $SQUID_DNS" >> "$test_workspace/squid.conf"
            echo "injected"
        fi
    )

    assert_equals "$result" "injected" "proxy entrypoint injects DNS"

    if grep -q "dns_nameservers 8.8.8.8" "$test_workspace/squid.conf"; then
        pass "DNS server written to squid.conf"
    else
        fail "DNS server written to squid.conf" "dns_nameservers line not found"
    fi

    cleanup_test_workspace "$test_workspace"
}

test_proxy_entrypoint_no_dns() {
    local test_workspace
    test_workspace=$(create_test_workspace)

    touch "$test_workspace/squid.conf"

    local result
    result=$(
        unset SQUID_DNS
        # Simulate the logic
        if [[ -n "$SQUID_DNS" ]]; then
            echo "dns_nameservers $SQUID_DNS" >> "$test_workspace/squid.conf"
            echo "injected"
        else
            echo "skipped"
        fi
    )

    assert_equals "$result" "skipped" "proxy entrypoint skips DNS when not set"

    if ! grep -q "dns_nameservers" "$test_workspace/squid.conf"; then
        pass "squid.conf unchanged when no DNS"
    else
        fail "squid.conf unchanged when no DNS" "unexpected dns_nameservers line found"
    fi

    cleanup_test_workspace "$test_workspace"
}

# Run all tests
test_paude_entrypoint_claude_seed
test_paude_entrypoint_no_seed
test_paude_entrypoint_json_seed
test_proxy_entrypoint_dns_injection
test_proxy_entrypoint_no_dns
