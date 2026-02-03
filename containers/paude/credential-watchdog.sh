#!/bin/bash
# Credential watchdog - removes gcloud credentials after inactivity
#
# This script runs in the background inside OpenShift pods and monitors for
# user inactivity. When the user disconnects (no tmux clients attached) and
# Claude is idle (low CPU), it removes gcloud credentials after a timeout.
#
# Credentials are automatically re-synced on next connect via connect_session().

TIMEOUT_MINUTES="${PAUDE_CREDENTIAL_TIMEOUT:-60}"
CHECK_INTERVAL="${PAUDE_CREDENTIAL_CHECK_INTERVAL:-60}"
ENABLED="${PAUDE_CREDENTIAL_WATCHDOG:-1}"
MIN_TIMEOUT=5
CPU_THRESHOLD=10

# Exit if disabled
if [[ "$ENABLED" != "1" ]]; then
    exit 0
fi

# Exit if /credentials doesn't exist (not on OpenShift or not using tmpfs credentials)
if [[ ! -d /credentials ]]; then
    exit 0
fi

# Enforce minimum timeout
if [[ "$TIMEOUT_MINUTES" -lt "$MIN_TIMEOUT" ]]; then
    TIMEOUT_MINUTES=$MIN_TIMEOUT
fi
INACTIVITY_THRESHOLD=$((TIMEOUT_MINUTES * 60))

has_tmux_clients() {
    # Check if a REMOTE client is attached to the claude session
    # PID 1 is always attached (container's main process runs "tmux attach")
    # Remote clients via "paude connect" / "oc exec" add additional clients
    local client_count

    client_count=$(tmux list-clients -t claude 2>/dev/null | wc -l)

    if [[ "$client_count" -gt 1 ]]; then
        echo "[watchdog] Remote client connected ($client_count clients total)" >&2
        return 0
    fi

    echo "[watchdog] No remote clients ($client_count client = container only)" >&2
    return 1
}

has_active_claude_process() {
    # Check if claude process is actively using CPU (indicates work in progress)
    local pid cpu cpu_int
    pid=$(pgrep -x claude 2>/dev/null | head -1)
    if [[ -z "$pid" ]]; then
        echo "[watchdog] No claude process found" >&2
        return 1
    fi
    cpu=$(ps -o %cpu= -p "$pid" 2>/dev/null | tr -d ' ')
    if [[ -z "$cpu" ]]; then
        echo "[watchdog] Could not get CPU for pid $pid" >&2
        return 1
    fi
    # Compare integer part only (bash doesn't do float comparison)
    cpu_int="${cpu%.*}"
    cpu_int="${cpu_int:-0}"  # Default to 0 if empty (e.g., ".5" becomes "")
    echo "[watchdog] Claude pid=$pid cpu=$cpu cpu_int=$cpu_int threshold=$CPU_THRESHOLD" >&2
    [[ "$cpu_int" -ge "$CPU_THRESHOLD" ]]
}

get_last_file_activity() {
    # Get the most recent modification time of activity-indicating files
    local newest=0 mtime
    for file in "$HOME/.claude/history.jsonl" "$HOME/.claude/debug/"*; do
        if [[ -f "$file" ]]; then
            mtime=$(stat -c %Y "$file" 2>/dev/null) || continue
            if [[ "$mtime" -gt "$newest" ]]; then
                newest=$mtime
            fi
        fi
    done
    echo "$newest"
}

delete_credentials() {
    # Remove gcloud credentials from tmpfs
    rm -rf /credentials/gcloud/* 2>/dev/null
    rm -f /credentials/.ready 2>/dev/null
    echo "[watchdog] Credentials removed after ${TIMEOUT_MINUTES}m inactivity" >&2
}

# Main loop
last_activity=$(date +%s)
echo "[watchdog] Started. timeout=${TIMEOUT_MINUTES}m check_interval=${CHECK_INTERVAL}s" >&2

while true; do
    sleep "$CHECK_INTERVAL"

    echo "[watchdog] Checking activity..." >&2

    # Never remove credentials if tmux clients are attached or Claude is actively working
    if has_tmux_clients; then
        echo "[watchdog] Tmux clients attached, resetting timer" >&2
        last_activity=$(date +%s)
        continue
    fi

    if has_active_claude_process; then
        echo "[watchdog] Claude is active (CPU >= ${CPU_THRESHOLD}%), resetting timer" >&2
        last_activity=$(date +%s)
        continue
    fi

    # Check file activity
    file_activity=$(get_last_file_activity)
    if [[ "$file_activity" -gt "$last_activity" ]]; then
        last_activity=$file_activity
    fi

    # Check if inactivity threshold has been exceeded
    now=$(date +%s)
    inactive=$((now - last_activity))

    echo "[watchdog] Inactive for ${inactive}s (threshold: ${INACTIVITY_THRESHOLD}s)" >&2

    if [[ "$inactive" -ge "$INACTIVITY_THRESHOLD" ]]; then
        delete_credentials
        # Exit after deletion; next connect_session() will restart the watchdog
        exit 0
    fi
done
