#!/bin/bash

# Copy seed files if provided (mounted read-only from host)
if [[ -d /tmp/claude.seed ]]; then
    cp -r /tmp/claude.seed/. /home/paude/.claude/
fi

if [[ -f /tmp/claude.json.seed ]]; then
    cp /tmp/claude.json.seed /home/paude/.claude.json
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

exec claude "$@"
