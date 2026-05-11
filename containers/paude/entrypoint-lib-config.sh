#!/bin/bash
# Agent config PVC persistence utilities for the paude entrypoint.
# Sourced by entrypoint-session.sh — not run standalone.

# Persist a dotfile directory from $HOME to /pvc.
# Creates symlink: $HOME/<dir> -> /pvc/<dir>
# On first start, copies image-baked contents to PVC; on reconnect, no-op.
persist_config_dir() {
    local dir_name="$1"
    if [[ ! -d /pvc ]]; then return 0; fi

    local pvc_dir="/pvc/$dir_name"
    local home_dir="$HOME/$dir_name"

    if [[ ! -d "$home_dir" ]] && [[ ! -L "$home_dir" ]] && [[ ! -d "$pvc_dir" ]]; then
        return 0
    fi

    mkdir -p "$pvc_dir" 2>/dev/null || true
    chmod g+rwX "$pvc_dir" 2>/dev/null || true
    chcon -R --reference=/pvc "$pvc_dir" 2>/dev/null || true

    if [[ ! -L "$home_dir" ]]; then
        if [[ -d "$home_dir" ]]; then
            cp -dR --preserve=mode,timestamps "$home_dir/." "$pvc_dir/" 2>/dev/null || true
            rm -rf "$home_dir" 2>/dev/null || true
        fi
        if [[ ! -e "$home_dir" ]]; then
            ln -sf "$pvc_dir" "$home_dir"
        else
            # Overlay FS may block removal of image-layer dirs on OpenShift.
            echo "persist_config_dir: cannot replace $home_dir with symlink; using PVC copy at $pvc_dir" >&2
        fi
    fi
}

# Persist agent config on the PVC volume so it survives container recreation.
# Creates symlinks: $HOME/$AGENT_CONFIG_DIR -> /pvc/$AGENT_CONFIG_DIR
#                    $HOME/$AGENT_CONFIG_FILE -> /pvc/$AGENT_CONFIG_FILE
persist_agent_config() {
    if [[ ! -d /pvc ]]; then
        return 0
    fi

    # Agent config dir is always needed, so ensure PVC side exists
    # before calling persist_config_dir (which skips absent dirs).
    mkdir -p "/pvc/$AGENT_CONFIG_DIR" 2>/dev/null || true
    persist_config_dir "$AGENT_CONFIG_DIR"

    # Config file (e.g., .claude.json) — symlink to PVC
    if [[ -n "$AGENT_CONFIG_FILE" ]]; then
        local pvc_config_file="/pvc/$AGENT_CONFIG_FILE"
        local home_config_file="$HOME/$AGENT_CONFIG_FILE"

        if [[ -f "$home_config_file" ]] && [[ ! -L "$home_config_file" ]]; then
            if [[ ! -f "$pvc_config_file" ]]; then
                cp -dR --preserve=mode,timestamps "$home_config_file" "$pvc_config_file" 2>/dev/null || true
            fi
            rm -f "$home_config_file" 2>/dev/null || true
        fi

        if [[ ! -f "$pvc_config_file" ]]; then
            echo '{}' > "$pvc_config_file" 2>/dev/null || true
        fi
        chmod g+rw "$pvc_config_file" 2>/dev/null || true
        chcon --reference=/pvc "$pvc_config_file" 2>/dev/null || true

        if [[ ! -e "$home_config_file" ]]; then
            ln -sf "$pvc_config_file" "$home_config_file"
        fi
    fi
}
