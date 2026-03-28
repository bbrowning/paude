#!/bin/bash
# Library: OpenClaw-specific helpers for entrypoint-session.sh

# Sets OPENCLAW_AUTH_TOKEN on success.
# Returns 0 if token found, 1 if not.
_read_openclaw_token() {
    local config_file="$HOME/.openclaw/openclaw.json"
    local token
    token=$(python3 -c "
import json, sys
try:
    with open('$config_file') as f:
        d = json.load(f)
    t = d.get('gateway', {}).get('auth', {}).get('token', '')
    if t:
        print(t)
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null)

    if [[ -n "$token" ]]; then
        OPENCLAW_AUTH_TOKEN="$token"
        export OPENCLAW_AUTH_TOKEN
        return 0
    fi
    return 1
}

# Usage: wait_for_openclaw_token [new|reconnect]
#   new       - poll up to 60s for the token (OpenClaw is starting up)
#   reconnect - read immediately (OpenClaw already running)
# Sets OPENCLAW_AUTH_TOKEN on success.
# Returns 0 if token found, 1 on timeout/not found.
wait_for_openclaw_token() {
    local session_type="${1:-reconnect}"

    if [[ "$session_type" == "reconnect" ]]; then
        _read_openclaw_token
        return $?
    fi

    local max_wait=60
    local interval=2
    local elapsed=0

    echo -n "Waiting for OpenClaw to start..."
    while (( elapsed < max_wait )); do
        if _read_openclaw_token; then
            echo " ready!"
            return 0
        fi
        echo -n "."
        sleep "$interval"
        (( elapsed += interval ))
    done

    echo " timed out after ${max_wait}s"
    echo "OpenClaw may still be starting. Check the logs in the tmux session."
    return 1
}

# Usage: show_openclaw_url [new|reconnect]
show_openclaw_url() {
    local session_type="${1:-reconnect}"

    wait_for_openclaw_token "$session_type"

    local token_suffix=""
    if [[ -n "${OPENCLAW_AUTH_TOKEN:-}" ]]; then
        token_suffix="/#token=${OPENCLAW_AUTH_TOKEN}"
    fi

    echo ""
    local IFS=";"
    for url in ${PAUDE_PORT_URLS:-}; do
        echo "  OpenClaw UI: ${url}${token_suffix}"
    done
    if [[ -z "$token_suffix" ]]; then
        echo "  (auth token not detected - you may need to authenticate manually)"
    fi
    echo ""
    read -r -p "Press Enter to view server logs..."
}
