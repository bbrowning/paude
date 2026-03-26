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

# Create .gitconfig if it doesn't exist (needed for git config --global)
touch "$HOME/.gitconfig" 2>/dev/null || true

# Fix git "dubious ownership" error when running as arbitrary UID (OpenShift restricted SCC)
git config --global --add safe.directory '*' 2>/dev/null || true

# Wait for a path to appear, polling every 2 seconds.
# Args: path, label, timeout_secs, on_timeout (exit|continue)
wait_for_path() {
    local path="$1"
    local label="$2"
    local timeout="$3"
    local on_timeout="${4:-exit}"  # "exit" or "continue"
    local elapsed=0

    while [[ ! -e "$path" ]]; do
        if [[ $elapsed -ge $timeout ]]; then
            if [[ "$on_timeout" == "continue" ]]; then
                echo "WARNING: Timed out waiting for $label, continuing anyway..." >&2
                return 0
            else
                echo "ERROR: Timed out waiting for $label" >&2
                exit 1
            fi
        fi
        if [[ $((elapsed % 10)) -eq 0 ]]; then
            echo "Waiting for $label... ($elapsed/${timeout}s)" >&2
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "${label^} ready." >&2
}

# Wait for credentials to be synced by the host (via oc cp)
wait_for_credentials() {
    # Only wait if /credentials exists (OpenShift with tmpfs-based credentials)
    if [[ ! -d /credentials ]]; then
        return 0
    fi
    wait_for_path "/credentials/.ready" "credentials" 300 "exit"
}

# Wait for git repository to be pushed (when PAUDE_WAIT_FOR_GIT=1)
# On OpenShift, git push happens after the pod starts. The agent captures
# git metadata at conversation init, so we must wait for .git before launching.
wait_for_git() {
    if [[ "${PAUDE_WAIT_FOR_GIT:-}" != "1" ]]; then
        return 0
    fi
    wait_for_path "/pvc/workspace/.git" "git repository" 120 "continue"
}

# Copy agent config from a source directory into $HOME.
# Handles: recursive copy, config file relocation, plugin permissions, OpenShift ownership.
# Args: source_path (directory containing agent config files)
copy_agent_config() {
    local source_path="$1"

    mkdir -p "$HOME/$AGENT_CONFIG_DIR"
    chmod g+rwX "$HOME/$AGENT_CONFIG_DIR" 2>/dev/null || true

    # Copy entire directory structure (--no-preserve=ownership for OpenShift arbitrary UID)
    cp -a --no-preserve=ownership "$source_path/." "$HOME/$AGENT_CONFIG_DIR/" 2>/dev/null || true

    # Handle config file specially - goes to ~/.<config_file>
    # Remove any image-baked config file first (may have wrong ownership on OpenShift)
    if [[ -n "$AGENT_CONFIG_FILE" ]] && [[ -n "$AGENT_CONFIG_FILE_BASENAME" ]] && [[ -f "$HOME/$AGENT_CONFIG_DIR/$AGENT_CONFIG_FILE_BASENAME" ]]; then
        rm -f "$HOME/$AGENT_CONFIG_FILE" 2>/dev/null || true
        mv "$HOME/$AGENT_CONFIG_DIR/$AGENT_CONFIG_FILE_BASENAME" "$HOME/$AGENT_CONFIG_FILE" 2>/dev/null || true
        chmod g+rw "$HOME/$AGENT_CONFIG_FILE" 2>/dev/null || true
    fi

    # Ensure plugins directory is writable (agent may update metadata)
    if [[ -d "$HOME/$AGENT_CONFIG_DIR/plugins" ]]; then
        chmod -R g+rwX "$HOME/$AGENT_CONFIG_DIR/plugins" 2>/dev/null || true
    fi

    # g+rwX sets read/write and execute on directories (X = execute only if dir)
    chmod -R g+rwX "$HOME/$AGENT_CONFIG_DIR" 2>/dev/null || true
}

# Set up credentials from tmpfs-based storage (/credentials)
setup_credentials() {
    local config_path="/credentials"

    # Only set up if /credentials exists (OpenShift with tmpfs volume)
    if [[ ! -d "$config_path" ]]; then
        return 0
    fi

    # Set up gcloud credentials via symlink
    if [[ -d "$config_path/gcloud" ]]; then
        mkdir -p "$HOME/.config"
        rm -rf "$HOME/.config/gcloud" 2>/dev/null || true
        ln -sf "$config_path/gcloud" "$HOME/.config/gcloud"
    fi

    # Copy agent config (need to be writable, so copy instead of symlink)
    if [[ -d "$config_path/$AGENT_NAME" ]]; then
        copy_agent_config "$config_path/$AGENT_NAME"
    fi

    # Set up gitconfig via symlink
    if [[ -f "$config_path/gitconfig" ]]; then
        rm -f "$HOME/.gitconfig" 2>/dev/null || true
        ln -sf "$config_path/gitconfig" "$HOME/.gitconfig"
    fi

    # Set up global gitignore via symlink
    if [[ -f "$config_path/gitignore-global" ]]; then
        mkdir -p "$HOME/.config/git"
        rm -f "$HOME/.config/git/ignore" 2>/dev/null || true
        ln -sf "$config_path/gitignore-global" "$HOME/.config/git/ignore"
    fi
}

# Wait for and set up tmpfs-based credentials
wait_for_credentials
setup_credentials
wait_for_git

# Start credential watchdog in background (OpenShift only)
# The watchdog removes credentials after inactivity when no tmux clients are attached
# Only start if not already running (avoid duplicates on reconnect)
if [[ -d /credentials ]] && [[ "${PAUDE_CREDENTIAL_WATCHDOG:-1}" == "1" ]]; then
    if ! pgrep -f "credential-watchdog.sh" >/dev/null 2>&1; then
        nohup /usr/local/bin/credential-watchdog.sh >> /tmp/credential-watchdog.log 2>&1 &
        echo "Credential watchdog started (timeout: ${PAUDE_CREDENTIAL_TIMEOUT:-60}m)"
    fi
fi

# Install agent if not already installed
# This allows the base image to work without the agent pre-installed
# The agent gets installed to the PVC so it persists across restarts
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

# Add PVC local bin to PATH (for agent and other tools installed to PVC)
# Also keep home .local/bin for tools installed during image build
export PATH="/pvc/.local/bin:$HOME/.local/bin:$PATH"

# Set up GitHub token from credentials file if available (OpenShift path)
if [[ -f /credentials/github_token ]]; then
    GH_TOKEN=$(cat /credentials/github_token)
    export GH_TOKEN
    export GH_CONFIG_DIR="/tmp/gh-config"
    mkdir -p "$GH_CONFIG_DIR" 2>/dev/null || true
fi
# Load secret environment variables from credentials tmpfs (OpenShift)
if [[ -d /credentials/env ]]; then
    for f in /credentials/env/*; do
        [[ -f "$f" ]] || continue
        varname=$(basename "$f")
        export "$varname"="$(cat "$f")"
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

# Attach to an existing tmux session.
# Used by both the reconnect early-exit and the normal attach path.
attach_to_session() {
    cd "${PAUDE_WORKSPACE:-/workspace}" 2>/dev/null || true
    echo "Attaching to existing $AGENT_NAME session..."
    exec tmux -u attach -t "$AGENT_SESSION_NAME"
}

# On reconnect (tmux session already exists), skip config copy and sandbox
# config — re-copying from host seed mounts would overwrite in-container
# state (prompt history, project data, conversation context).
if tmux -u has-session -t "$AGENT_SESSION_NAME" 2>/dev/null; then
    attach_to_session
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

# Suppress interactive prompts in sandboxed containers
# If a generated sandbox config script exists, source it; otherwise use built-in logic
apply_sandbox_config() {
    if [[ "${PAUDE_SUPPRESS_PROMPTS:-}" != "1" ]]; then
        return 0
    fi

    # Check for agent-generated sandbox config script
    if [[ -f /tmp/agent-sandbox-config.sh ]]; then
        source /tmp/agent-sandbox-config.sh
        return 0
    fi

    # Fallback: built-in sandbox config keyed on agent
    local workspace="${PAUDE_WORKSPACE:-/workspace}"

    case "$AGENT_NAME" in
        gemini)
            # Pre-trust workspace folder so Gemini doesn't prompt on every connect
            local trusted_json="$HOME/$AGENT_CONFIG_DIR/trustedFolders.json"
            mkdir -p "$HOME/$AGENT_CONFIG_DIR" 2>/dev/null || true
            if [[ -f "$trusted_json" ]]; then
                jq --arg ws "$workspace" '. + {($ws): "TRUST_FOLDER"}' \
                    "$trusted_json" > "${trusted_json}.tmp" \
                    && mv "${trusted_json}.tmp" "$trusted_json"
            else
                jq -n --arg ws "$workspace" '{($ws): "TRUST_FOLDER"}' > "$trusted_json"
            fi
            chmod g+rw "$trusted_json" 2>/dev/null || true
            ;;
        cursor)
            # Cursor CLI sandbox config
            local cli_config="$HOME/.cursor/cli-config.json"
            mkdir -p "$HOME/.cursor" 2>/dev/null || true

            # Seed from host cli-config.json if available (carries auth tokens)
            if [[ -f /tmp/cursor-cli-config.seed ]]; then
                cp /tmp/cursor-cli-config.seed "$cli_config"
                chmod g+rw "$cli_config" 2>/dev/null || true
            fi

            # Ensure version field exists so CLI doesn't prompt for setup,
            # and force HTTP/1.1 for agent inference (HTTP/2 bypasses proxy).
            if [[ -f "$cli_config" ]]; then
                jq '. * {"version": (.version // 1), "network": {"useHttp1ForAgent": true}}' \
                    "$cli_config" > "${cli_config}.tmp" \
                    && mv "${cli_config}.tmp" "$cli_config"
            else
                jq -n '{"version": 1, "network": {"useHttp1ForAgent": true}}' > "$cli_config"
            fi
            chmod g+rw "$cli_config" 2>/dev/null || true

            # Sync Cursor auth.json (accessToken/refreshToken) from host
            mkdir -p "$HOME/.config/cursor" 2>/dev/null || true
            # Podman path: seed file bind-mounted at /tmp/
            if [[ -f /tmp/cursor-auth.seed ]]; then
                cp /tmp/cursor-auth.seed "$HOME/.config/cursor/auth.json"
                chmod g+rw "$HOME/.config/cursor/auth.json" 2>/dev/null || true
            fi
            # OpenShift path: synced to /credentials/ by sync.py
            if [[ -f /credentials/cursor-auth.json ]]; then
                cp /credentials/cursor-auth.json "$HOME/.config/cursor/auth.json"
                chmod g+rw "$HOME/.config/cursor/auth.json" 2>/dev/null || true
            fi

            # Pre-trust workspace folder so Cursor doesn't prompt on every connect
            local ws_slug
            ws_slug="${workspace//\//-}"
            ws_slug="${ws_slug#-}"
            local trusted_dir="$HOME/.cursor/projects/$ws_slug"
            mkdir -p "$trusted_dir" 2>/dev/null || true
            cat > "$trusted_dir/.workspace-trusted" <<TRUST
{
  "trustedAt": "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)",
  "workspacePath": "$workspace"
}
TRUST
            ;;
        *)
            # Claude Code sandbox config
            local config_file="$HOME/$AGENT_CONFIG_FILE"
            local settings_json="$HOME/$AGENT_CONFIG_DIR/settings.json"
            local host_ws="${PAUDE_HOST_WORKSPACE:-}"

            # Suppress trust prompt and onboarding, rewriting host project entry
            if [[ -f "$config_file" ]]; then
                jq --arg ws "$workspace" --arg host_ws "$host_ws" '
                    (.projects[$host_ws] // {}) as $host_data |
                    ($host_data * {hasTrustDialogAccepted: true}) as $ws_entry |
                    .hasCompletedOnboarding = true |
                    .projects = {($ws): $ws_entry}
                ' "$config_file" > "${config_file}.tmp" \
                    && mv "${config_file}.tmp" "$config_file"
            else
                jq -n --arg ws "$workspace" '{
                    hasCompletedOnboarding: true,
                    projects: {($ws): {hasTrustDialogAccepted: true}}
                }' > "$config_file"
            fi
            chmod g+rw "$config_file" 2>/dev/null || true

            # Suppress bypass permissions warning when yolo flag is in args
            if [[ "${AGENT_ARGS:-}" == *"--dangerously-skip-permissions"* ]]; then
                mkdir -p "$HOME/$AGENT_CONFIG_DIR" 2>/dev/null || true
                local skip_patch='{"skipDangerousModePermissionPrompt": true}'
                if [[ -f "$settings_json" ]]; then
                    jq --argjson patch "$skip_patch" '. * $patch' "$settings_json" > "${settings_json}.tmp" \
                        && mv "${settings_json}.tmp" "$settings_json"
                else
                    echo "$skip_patch" > "$settings_json"
                fi
                chmod g+rw "$settings_json" 2>/dev/null || true
            fi
            ;;
    esac
}

apply_sandbox_config 2>>/tmp/sandbox-config.log || echo "apply_sandbox_config failed: $?" >> /tmp/sandbox-config.log

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
    attach_to_session
else
    echo "Starting new $AGENT_NAME session..."
    tmux -u new-session -s "$AGENT_SESSION_NAME" -c "$WORKSPACE" -d "bash -l"
    tmux send-keys -t "$AGENT_SESSION_NAME" "export HOME=$HOME PATH='$PATH'" Enter
    tmux send-keys -t "$AGENT_SESSION_NAME" "cd $WORKSPACE" Enter
    tmux send-keys -t "$AGENT_SESSION_NAME" "clear && $AGENT_LAUNCH_CMD $AGENT_ARGS" Enter
    attach_to_session
fi
