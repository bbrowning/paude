#!/bin/bash
# Agent installation utilities for the paude entrypoint.
# Sourced by entrypoint-session.sh — not run standalone.

# Check if the agent binary is available in any known location
agent_binary_exists() {
    [[ -x "/pvc/.local/bin/$AGENT_PROCESS" ]] \
        || [[ -x "$HOME/.local/bin/$AGENT_PROCESS" ]] \
        || command -v "$AGENT_PROCESS" >/dev/null 2>&1
}

install_agent() {
    # Check if agent is already installed and executable
    if agent_binary_exists; then
        return 0
    fi

    echo "Installing $AGENT_NAME to PVC..." >&2

    # Set up installation directory in PVC for persistence
    mkdir -p /pvc/.local/bin
    export CLAUDE_INSTALL_DIR=/pvc/.local

    # Install using the agent's install script
    # Enable pipefail so curl|bash failures propagate (restore after)
    set -o pipefail
    if eval "$AGENT_INSTALL_SCRIPT" 2>&1; then
        echo "$AGENT_NAME installed successfully." >&2
    else
        echo "ERROR: Failed to install $AGENT_NAME. You may need to install it manually." >&2
        set +o pipefail
        return 1
    fi
    set +o pipefail

    # Verify the agent binary actually exists (defense against silent install failures)
    if agent_binary_exists; then
        return 0
    fi
    echo "ERROR: $AGENT_NAME installation failed — binary not found after install." >&2
    return 1
}
