#!/bin/bash
# Agent config PVC persistence utilities for the paude entrypoint.
# Sourced by entrypoint-session.sh — not run standalone.

# Persist agent config on the PVC volume so it survives container recreation.
# Creates symlinks: $HOME/$AGENT_CONFIG_DIR -> /pvc/$AGENT_CONFIG_DIR
#                    $HOME/$AGENT_CONFIG_FILE -> /pvc/$AGENT_CONFIG_FILE
# Follows the same pattern as agent binary persistence at /pvc/.local/bin.
persist_agent_config() {
    # Skip if /pvc doesn't exist (non-persistent setup)
    if [[ ! -d /pvc ]]; then
        return 0
    fi

    local pvc_config_dir="/pvc/$AGENT_CONFIG_DIR"
    local home_config_dir="$HOME/$AGENT_CONFIG_DIR"

    # Create PVC directory if it doesn't exist (first start)
    mkdir -p "$pvc_config_dir" 2>/dev/null || true
    chmod g+rwX "$pvc_config_dir" 2>/dev/null || true
    # Fix SELinux context on PVC config dir — earlier versions of cp -a
    # preserved the image filesystem context, making the dir inaccessible.
    chcon -R --reference=/pvc "$pvc_config_dir" 2>/dev/null || true

    # If HOME config dir is a real directory (not symlink), merge into PVC and replace with symlink
    if [[ ! -L "$home_config_dir" ]]; then
        if [[ -d "$home_config_dir" ]]; then
            cp -dR --preserve=mode,timestamps "$home_config_dir/." "$pvc_config_dir/" 2>/dev/null || true
        fi
        rm -rf "$home_config_dir" 2>/dev/null || true
        ln -sf "$pvc_config_dir" "$home_config_dir"
    fi

    # Config file (e.g., .claude.json) — symlink to PVC
    if [[ -n "$AGENT_CONFIG_FILE" ]]; then
        local pvc_config_file="/pvc/$AGENT_CONFIG_FILE"
        local home_config_file="$HOME/$AGENT_CONFIG_FILE"

        # If HOME config file is a real file (not symlink), move to PVC
        if [[ -f "$home_config_file" ]] && [[ ! -L "$home_config_file" ]]; then
            if [[ ! -f "$pvc_config_file" ]]; then
                cp -dR --preserve=mode,timestamps "$home_config_file" "$pvc_config_file" 2>/dev/null || true
            fi
            rm -f "$home_config_file"
        fi

        # Create PVC file if it doesn't exist
        if [[ ! -f "$pvc_config_file" ]]; then
            echo '{}' > "$pvc_config_file" 2>/dev/null || true
        fi
        chmod g+rw "$pvc_config_file" 2>/dev/null || true
        chcon --reference=/pvc "$pvc_config_file" 2>/dev/null || true

        # Create symlink
        if [[ ! -L "$home_config_file" ]]; then
            rm -f "$home_config_file" 2>/dev/null || true
            ln -sf "$pvc_config_file" "$home_config_file"
        fi
    fi
}
