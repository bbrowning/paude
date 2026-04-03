#!/bin/bash
set -e

# Entrypoint for persistent sessions (Podman and OpenShift)
# Handles: HOME setup, credentials from tmpfs, agent startup
# All agent-specific behavior is driven by PAUDE_AGENT_* env vars.

# Agent configuration (defaults to Claude Code for backward compatibility)
AGENT_NAME="${PAUDE_AGENT_NAME:-claude}"
AGENT_PROCESS="${PAUDE_AGENT_PROCESS:-claude}"
AGENT_CONFIG_DIR="${PAUDE_AGENT_CONFIG_DIR:-.claude}"
AGENT_CONFIG_FILE="${PAUDE_AGENT_CONFIG_FILE:-.claude.json}"
AGENT_INSTALL_SCRIPT="${PAUDE_AGENT_INSTALL_SCRIPT:-curl -fsSL https://claude.ai/install.sh | bash}"
AGENT_SESSION_NAME="${PAUDE_AGENT_SESSION_NAME:-claude}"
AGENT_LAUNCH_CMD="${PAUDE_AGENT_LAUNCH_CMD:-claude}"
AGENT_SEED_DIR="${PAUDE_AGENT_SEED_DIR:-/tmp/claude.seed}"
AGENT_SEED_FILE="${PAUDE_AGENT_SEED_FILE:-/tmp/claude.json.seed}"
# Derive basename for config file (e.g., ".claude.json" -> "claude.json")
AGENT_CONFIG_FILE_BASENAME="${AGENT_CONFIG_FILE#.}"
# Backward compat: PAUDE_AGENT_ARGS > PAUDE_CLAUDE_ARGS > positional args
AGENT_ARGS="${PAUDE_AGENT_ARGS:-${PAUDE_CLAUDE_ARGS:-$*}}"

# Source library functions
source /usr/local/bin/entrypoint-lib-credentials.sh
source /usr/local/bin/entrypoint-lib-config.sh
source /usr/local/bin/entrypoint-lib-install.sh
# Optional: OpenClaw helpers (may not exist in older container images)
if [[ -f /usr/local/bin/entrypoint-lib-openclaw.sh ]]; then
    source /usr/local/bin/entrypoint-lib-openclaw.sh
fi

# Ensure HOME is set correctly for OpenShift arbitrary UID
# OpenShift runs containers with random UIDs that don't exist in /etc/passwd
# HOME may be unset, empty, or set to "/" which is not writable
if [[ -z "$HOME" || "$HOME" == "/" ]]; then
    export HOME="/home/paude"
fi

# Ensure home directory exists and is writable, fall back to /tmp if needed
if ! mkdir -p "$HOME" 2>/dev/null || ! touch "$HOME/.test" 2>/dev/null; then
    export HOME="/tmp/paude-home"
    mkdir -p "$HOME"
fi
rm -f "$HOME/.test" 2>/dev/null || true

# Ensure all home directories are group-writable for OpenShift arbitrary UID
chmod -R g+rwX "$HOME" 2>/dev/null || true

# Make PVC mount group-writable for OpenShift (PVC mounted at /pvc)
# The paude user is in group 0, so g+rwX allows write access
if [[ -d /pvc ]]; then
    chmod g+rwX /pvc 2>/dev/null || true
fi

# Fix git "dubious ownership" error when running as arbitrary UID (OpenShift restricted SCC)
# git config --global creates .gitconfig if it doesn't exist
git config --global --add safe.directory '*' 2>/dev/null || true

# Update CA trust early (before any HTTPS calls like agent install)
# The CA cert is injected by the host after the container starts.
setup_ca_trust

# Wait for and set up tmpfs-based credentials
# Order matters: setup_credentials copies host config into ~/.claude (real dir),
# then persist_agent_config merges it into /pvc/.claude (preserving existing
# runtime state like sessions/history) and symlinks ~/.claude -> /pvc/.claude.
wait_for_credentials
setup_credentials
persist_agent_config
wait_for_git

# Add PVC local bin to PATH (for agent and other tools installed to PVC)
# Also keep home .local/bin for tools installed during image build
export PATH="/pvc/.local/bin:$HOME/.local/bin:$PATH"

# Set up GitHub token from credentials file if available (OpenShift path)
if [[ -f /credentials/github_token ]]; then
    GH_TOKEN=$(<"/credentials/github_token")
    export GH_TOKEN
    export GH_CONFIG_DIR="/tmp/gh-config"
    mkdir -p "$GH_CONFIG_DIR" 2>/dev/null || true
fi
# Load secret environment variables from credentials tmpfs (OpenShift)
if [[ -d /credentials/env ]]; then
    for f in /credentials/env/*; do
        [[ -f "$f" ]] || continue
        varname="${f##*/}"
        export "$varname"="$(<"$f")"
    done
fi

# For Podman: GH_TOKEN may be set via podman exec -e; just ensure GH_CONFIG_DIR is set
if [[ -n "${GH_TOKEN:-}" ]] && [[ -z "${GH_CONFIG_DIR:-}" ]]; then
    export GH_CONFIG_DIR="/tmp/gh-config"
    mkdir -p "$GH_CONFIG_DIR" 2>/dev/null || true
fi

# Install agent if needed (skip if PAUDE_SKIP_AGENT_INSTALL or legacy PAUDE_SKIP_CLAUDE_INSTALL is set)
if [[ -z "${PAUDE_SKIP_AGENT_INSTALL:-}" ]] && [[ -z "${PAUDE_SKIP_CLAUDE_INSTALL:-}" ]]; then
    install_agent
fi

# Set up terminal environment for tmux
# Must be set before any tmux calls — OpenShift arbitrary UIDs default
# SHELL to /sbin/nologin, which causes tmux to fail on session creation.
export TERM=xterm-256color
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export SHELL=/bin/bash

# In headless mode (PAUDE_HEADLESS=1), exit after starting/detecting the
# tmux session instead of attaching an interactive terminal.
exit_if_headless() {
    if [[ "${PAUDE_HEADLESS:-}" == "1" ]]; then
        echo "$AGENT_NAME session $1 (headless)."
        exit 0
    fi
}

# Attach to an existing tmux session.
# Used by both the reconnect early-exit and the normal attach path.
attach_to_session() {
    local session_type="${1:-reconnect}"
    cd "${PAUDE_WORKSPACE:-/workspace}" 2>/dev/null || true
    echo "Attaching to existing $AGENT_NAME session..."

    # For OpenClaw: show auth URL and wait for Enter before attaching
    if [[ "$AGENT_NAME" == "openclaw" ]] && [[ -n "${PAUDE_PORT_URLS:-}" ]] && type show_openclaw_url &>/dev/null; then
        show_openclaw_url "$session_type"
    elif [[ -n "${PAUDE_PORT_URLS:-}" ]]; then
        # Show port-forward URLs in tmux status line after attaching.
        # The background process survives exec and fires after the client attaches.
        (sleep 1 && tmux display-message -t "$AGENT_SESSION_NAME" -d 5000 "${PAUDE_PORT_URLS//;/  |  }") &
    fi
    exec tmux -u attach -t "$AGENT_SESSION_NAME"
}

# On reconnect (tmux session already exists), skip config copy and sandbox
# config — re-copying from host seed mounts would overwrite in-container
# state (prompt history, project data, conversation context).
if tmux -u has-session -t "$AGENT_SESSION_NAME" 2>/dev/null; then
    exit_if_headless "already running"
    attach_to_session reconnect
fi

# Legacy: Copy seed files if provided via Secret mount (Podman backend fallback)
if [[ -d "$AGENT_SEED_DIR" ]] && [[ ! -d /credentials ]]; then
    copy_agent_config "$AGENT_SEED_DIR"
fi

# Also check for separate config file seed mount (Podman backend)
if [[ -n "$AGENT_SEED_FILE" ]] && { [[ -f "$AGENT_SEED_FILE" ]] || [[ -L "$AGENT_SEED_FILE" ]]; }; then
    if [[ -n "$AGENT_CONFIG_FILE" ]]; then
        cp -L "$AGENT_SEED_FILE" "$HOME/$AGENT_CONFIG_FILE" 2>/dev/null || true
        chmod g+rw "$HOME/$AGENT_CONFIG_FILE" 2>/dev/null || true
    fi
fi

# Apply agent sandbox config (generated by Python, synced before entrypoint runs)
_SANDBOX_CFG="$HOME/.paude/agent-sandbox-config.sh"
if [[ "${PAUDE_SUPPRESS_PROMPTS:-}" == "1" ]] && [[ -f "$_SANDBOX_CFG" ]]; then
    source "$_SANDBOX_CFG" 2>>/tmp/sandbox-config.log \
        || echo "agent-sandbox-config.sh failed: $?" >> /tmp/sandbox-config.log
fi

# Session workspace setup
# For persistent sessions, workspace is at /workspace (mounted volume)
WORKSPACE="${PAUDE_WORKSPACE:-/workspace}"

# Create workspace directory if it doesn't exist
mkdir -p "$WORKSPACE" 2>/dev/null || true
chmod g+rwX "$WORKSPACE" 2>/dev/null || true

# Fix workspace config directory if it exists (synced from host)
if [[ -d "$WORKSPACE/$AGENT_CONFIG_DIR" ]]; then
    chmod -R g+rwX "$WORKSPACE/$AGENT_CONFIG_DIR" 2>/dev/null || true
fi

if tmux -u has-session -t "$AGENT_SESSION_NAME" 2>/dev/null; then
    exit_if_headless "already running"
    attach_to_session reconnect
else
    echo "Starting new $AGENT_NAME session..."
    tmux -u new-session -s "$AGENT_SESSION_NAME" -c "$WORKSPACE" -d "bash -l"
    tmux send-keys -t "$AGENT_SESSION_NAME" "export HOME=$HOME PATH='$PATH'" Enter
    tmux send-keys -t "$AGENT_SESSION_NAME" "cd $WORKSPACE" Enter
    tmux send-keys -t "$AGENT_SESSION_NAME" "clear && $AGENT_LAUNCH_CMD $AGENT_ARGS" Enter
    exit_if_headless "started"
    attach_to_session new
fi
