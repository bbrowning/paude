#!/bin/bash

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
# Directories created during image build may not have group-write permissions
chmod -R g+rwX "$HOME" 2>/dev/null || true

# Also fix workspace .claude directory if it exists (synced from host)
if [[ -d /workspace/.claude ]]; then
    chmod -R g+rwX /workspace/.claude 2>/dev/null || true
fi

# Create .gitconfig if it doesn't exist (needed for git config --global)
touch "$HOME/.gitconfig" 2>/dev/null || true

# Fix git "dubious ownership" error when running as arbitrary UID (OpenShift restricted SCC)
# Git 2.35.2+ refuses to operate if file ownership doesn't match running UID
git config --global --add safe.directory '*' 2>/dev/null || true

# Copy seed files if provided (mounted read-only from host)
# Kubernetes mounts secrets as symlinks, so we copy each file individually
# to avoid copying internal ..data directories
if [[ -d /tmp/claude.seed ]]; then
    mkdir -p "$HOME/.claude"
    # Ensure group-writable for OpenShift arbitrary UID (runs as GID 0)
    chmod g+rwX "$HOME/.claude" 2>/dev/null || true
    for f in /tmp/claude.seed/*; do
        if [[ -f "$f" || -L "$f" ]]; then
            filename=$(basename "$f")
            # claude.json goes to ~/.claude.json (root of home), not inside .claude/
            if [[ "$filename" == "claude.json" ]]; then
                cp -L "$f" "$HOME/.claude.json" 2>/dev/null || true
                chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
            else
                cp -L "$f" "$HOME/.claude/" 2>/dev/null || true
            fi
        fi
    done
    # Ensure all copied files are group-readable/writable
    chmod -R g+rw "$HOME/.claude" 2>/dev/null || true
fi

# Also check for separate claude.json.seed mount (Podman backend)
if [[ -f /tmp/claude.json.seed ]] || [[ -L /tmp/claude.json.seed ]]; then
    cp -L /tmp/claude.json.seed "$HOME/.claude.json" 2>/dev/null || true
    chmod g+rw "$HOME/.claude.json" 2>/dev/null || true
fi

# Create symlinks from shadowed venv directories to /opt/venv
# PAUDE_VENV_PATHS is a colon-separated list of venv paths
if [[ -n "${PAUDE_VENV_PATHS:-}" && -d /opt/venv ]]; then
    IFS=':' read -ra VENV_PATHS <<< "$PAUDE_VENV_PATHS"
    for venv_path in "${VENV_PATHS[@]}"; do
        if [[ -d "$venv_path" ]]; then
            # Create symlinks for common venv subdirectories
            for subdir in bin lib include lib64 pyvenv.cfg; do
                if [[ -e "/opt/venv/$subdir" ]]; then
                    ln -sf "/opt/venv/$subdir" "$venv_path/$subdir"
                fi
            done
        fi
    done

    # Activate the venv for Claude Code's shell commands
    export VIRTUAL_ENV=/opt/venv
    export PATH="/opt/venv/bin:$PATH"
    unset PYTHON_HOME
fi

# Get claude args from environment or command line
CLAUDE_ARGS="${PAUDE_CLAUDE_ARGS:-$*}"

# Check if we have a TTY for tmux
if [ -t 0 ] && [ -t 1 ]; then
    # Run Claude in tmux for session persistence
    SESSION_NAME="claude"

    # Set up terminal environment for tmux
    export TERM="${TERM:-xterm-256color}"
    export SHELL=/bin/bash

    if tmux -u has-session -t "$SESSION_NAME" 2>/dev/null; then
        echo "Attaching to existing Claude session..."
        exec tmux -u attach -t "$SESSION_NAME"
    else
        echo "Starting new Claude session..."
        # Start tmux with login shell for proper terminal initialization
        tmux -u new-session -s "$SESSION_NAME" -d "bash -l"
        # Set up environment
        tmux send-keys -t "$SESSION_NAME" "export HOME=$HOME PATH=$HOME/.local/bin:\$PATH" Enter
        # Set up venv if applicable
        if [[ -n "${PAUDE_VENV_PATHS:-}" && -d /opt/venv ]]; then
            tmux send-keys -t "$SESSION_NAME" "export VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:\$PATH" Enter
        fi
        tmux send-keys -t "$SESSION_NAME" "claude $CLAUDE_ARGS" Enter
        exec tmux -u attach -t "$SESSION_NAME"
    fi
else
    # No TTY available, run claude directly
    exec claude $CLAUDE_ARGS
fi
