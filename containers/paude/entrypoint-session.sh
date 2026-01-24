#!/bin/bash
set -e

# Entrypoint for persistent sessions (Podman and OpenShift)
# Handles: HOME setup, credentials from PVC, venv creation, dependency installation, Claude startup

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

# Wait for credentials to be synced by the host (via oc cp)
# The host creates /pvc/config/.ready when sync is complete
wait_for_credentials() {
    local ready_file="/pvc/config/.ready"
    local timeout=300
    local elapsed=0

    # Only wait if /pvc exists (OpenShift with PVC-based credentials)
    if [[ ! -d /pvc ]]; then
        return 0
    fi

    while [[ ! -f "$ready_file" ]]; do
        if [[ $elapsed -ge $timeout ]]; then
            echo "ERROR: Timed out waiting for credentials sync" >&2
            exit 1
        fi
        if [[ $((elapsed % 10)) -eq 0 ]]; then
            echo "Waiting for credentials... ($elapsed/${timeout}s)" >&2
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    echo "Credentials ready." >&2
}

# Set up credentials from PVC-based storage
setup_credentials_from_pvc() {
    local config_path="/pvc/config"

    # Only set up if /pvc/config exists
    if [[ ! -d "$config_path" ]]; then
        return 0
    fi

    # Set up gcloud credentials via symlink
    if [[ -d "$config_path/gcloud" ]]; then
        mkdir -p "$HOME/.config"
        rm -rf "$HOME/.config/gcloud" 2>/dev/null || true
        ln -sf "$config_path/gcloud" "$HOME/.config/gcloud"
    fi

    # Copy claude config (need to be writable, so copy instead of symlink)
    if [[ -d "$config_path/claude" ]]; then
        mkdir -p "$HOME/.claude"
        chmod g+rwX "$HOME/.claude" 2>/dev/null || true

        # Copy entire synced directory structure
        cp -a "$config_path/claude/." "$HOME/.claude/" 2>/dev/null || true

        # Handle claude.json specially - goes to ~/.claude.json
        if [[ -f "$HOME/.claude/claude.json" ]]; then
            mv "$HOME/.claude/claude.json" "$HOME/.claude.json" 2>/dev/null || true
            chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
        fi

        # Ensure plugins directory is writable (Claude may update metadata)
        if [[ -d "$HOME/.claude/plugins" ]]; then
            chmod -R g+rwX "$HOME/.claude/plugins" 2>/dev/null || true
        fi

        # g+rwX sets read/write and execute on directories (X = execute only if dir)
        chmod -R g+rwX "$HOME/.claude" 2>/dev/null || true
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

# Wait for and set up PVC-based credentials
wait_for_credentials
setup_credentials_from_pvc

# Legacy: Copy seed files if provided via Secret mount (Podman backend fallback)
if [[ -d /tmp/claude.seed ]] && [[ ! -d /pvc/config ]]; then
    mkdir -p "$HOME/.claude"
    chmod g+rwX "$HOME/.claude" 2>/dev/null || true
    for f in /tmp/claude.seed/*; do
        if [[ -f "$f" || -L "$f" ]]; then
            filename=$(basename "$f")
            if [[ "$filename" == "claude.json" ]]; then
                cp -L "$f" "$HOME/.claude.json" 2>/dev/null || true
                chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
            else
                cp -L "$f" "$HOME/.claude/" 2>/dev/null || true
            fi
        fi
    done
    chmod -R g+rw "$HOME/.claude" 2>/dev/null || true
fi

# Also check for separate claude.json.seed mount (Podman backend)
if [[ -f /tmp/claude.json.seed ]] || [[ -L /tmp/claude.json.seed ]]; then
    cp -L /tmp/claude.json.seed "$HOME/.claude.json" 2>/dev/null || true
    chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
fi

# Session workspace setup
# For persistent sessions, workspace is at /workspace (mounted volume)
WORKSPACE="${PAUDE_WORKSPACE:-/workspace}"
VENV_PATH="$WORKSPACE/.venv"
UV_CACHE="$WORKSPACE/.uv-cache"
REQUIREMENTS_FILE="$WORKSPACE/requirements.txt"
HASH_FILE="$VENV_PATH/.requirements-hash"

# Create workspace directory if it doesn't exist
mkdir -p "$WORKSPACE" 2>/dev/null || true
chmod g+rwX "$WORKSPACE" 2>/dev/null || true

# Fix workspace .claude directory if it exists (synced from host)
if [[ -d "$WORKSPACE/.claude" ]]; then
    chmod -R g+rwX "$WORKSPACE/.claude" 2>/dev/null || true
fi

# Set up uv cache directory
export UV_CACHE_DIR="$UV_CACHE"
mkdir -p "$UV_CACHE" 2>/dev/null || true

# Function to compute requirements.txt hash
compute_requirements_hash() {
    if [[ -f "$REQUIREMENTS_FILE" ]]; then
        sha256sum "$REQUIREMENTS_FILE" 2>/dev/null | cut -d' ' -f1
    else
        echo "no-requirements"
    fi
}

# Function to get installed hash
get_installed_hash() {
    if [[ -f "$HASH_FILE" ]]; then
        cat "$HASH_FILE" 2>/dev/null || echo ""
    else
        echo ""
    fi
}

# Create or update virtual environment if requirements.txt exists
if [[ -f "$REQUIREMENTS_FILE" ]]; then
    CURRENT_HASH=$(compute_requirements_hash)
    INSTALLED_HASH=$(get_installed_hash)

    # Create venv if it doesn't exist
    if [[ ! -d "$VENV_PATH" ]]; then
        echo "Creating virtual environment at $VENV_PATH..."
        python3 -m venv "$VENV_PATH"
        chmod -R g+rwX "$VENV_PATH" 2>/dev/null || true
        INSTALLED_HASH=""  # Force reinstall
    fi

    # Install dependencies if hash changed
    if [[ "$CURRENT_HASH" != "$INSTALLED_HASH" ]]; then
        echo "Installing dependencies with uv..."
        if "$HOME/.local/bin/uv" pip install --python "$VENV_PATH/bin/python" -r "$REQUIREMENTS_FILE"; then
            echo "$CURRENT_HASH" > "$HASH_FILE"
            chmod g+rw "$HASH_FILE" 2>/dev/null || true
            echo "Dependencies installed successfully."
        else
            echo "Warning: Failed to install some dependencies."
        fi
    else
        echo "Dependencies up to date."
    fi

    # Activate virtual environment for subsequent commands
    export VIRTUAL_ENV="$VENV_PATH"
    export PATH="$VENV_PATH/bin:$PATH"
    unset PYTHON_HOME
fi

# Handle venv shadowing (for workspace mounts that have their own venv)
if [[ -n "${PAUDE_VENV_PATHS:-}" && -d /opt/venv ]]; then
    IFS=':' read -ra VENV_PATHS <<< "$PAUDE_VENV_PATHS"
    for venv_path in "${VENV_PATHS[@]}"; do
        if [[ -d "$venv_path" ]]; then
            for subdir in bin lib include lib64 pyvenv.cfg; do
                if [[ -e "/opt/venv/$subdir" ]]; then
                    ln -sf "/opt/venv/$subdir" "$venv_path/$subdir"
                fi
            done
        fi
    done
fi

# Get claude args from environment or command line
CLAUDE_ARGS="${PAUDE_CLAUDE_ARGS:-$*}"

SESSION_NAME="claude"

# Set up terminal environment for tmux
export TERM="${TERM:-xterm-256color}"

# Set UTF-8 locale for proper character rendering
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

# Explicitly set SHELL for tmux
export SHELL=/bin/bash

# Change to workspace directory
cd "$WORKSPACE" 2>/dev/null || true

if tmux -u has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Attaching to existing Claude session..."
    exec tmux -u attach -t "$SESSION_NAME"
else
    echo "Starting new Claude session..."
    tmux -u new-session -s "$SESSION_NAME" -d "bash -l"
    tmux send-keys -t "$SESSION_NAME" "export HOME=$HOME PATH=$HOME/.local/bin:\$PATH" Enter
    if [[ -d "$VENV_PATH" ]]; then
        tmux send-keys -t "$SESSION_NAME" "source $VENV_PATH/bin/activate" Enter
    fi
    tmux send-keys -t "$SESSION_NAME" "cd $WORKSPACE" Enter
    tmux send-keys -t "$SESSION_NAME" "claude $CLAUDE_ARGS" Enter
    exec tmux -u attach -t "$SESSION_NAME"
fi
