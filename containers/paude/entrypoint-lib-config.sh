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
    # If the PVC target could not be created (e.g. /pvc is not writable by this
    # user because the runtime UID drifted from the volume's owner), do NOT
    # remove the seeded $home_dir or point a symlink at a nonexistent target —
    # that leaves a broken symlink. Leave $home_dir in place and warn instead.
    if [[ ! -d "$pvc_dir" ]]; then
        echo "persist_config_dir: cannot create $pvc_dir (is /pvc writable by $(id -un)?); leaving $home_dir in place" >&2
        return 0
    fi
    chmod g+rwX "$pvc_dir" 2>/dev/null || true
    chcon -R --reference=/pvc "$pvc_dir" 2>/dev/null || true

    if [[ ! -L "$home_dir" ]]; then
        if [[ -d "$home_dir" ]]; then
            # Image-baked files seed missing PVC state but never replace state
            # that already survived a restart or container recreation.
            cp -dRn --preserve=mode,timestamps "$home_dir/." "$pvc_dir/" 2>/dev/null || true
            rm -rf "$home_dir" 2>/dev/null || true
        fi
        if [[ ! -e "$home_dir" ]]; then
            ln -sf "$pvc_dir" "$home_dir"
        else
            # Overlay filesystems may block removal of image-layer directories.
            echo "persist_config_dir: cannot replace $home_dir with symlink; using PVC copy at $pvc_dir" >&2
        fi
    fi
}

# Persist a config file from $HOME to /pvc and replace it with a symlink.
persist_config_file() {
    local file_name="$1"
    local pvc_config_file="/pvc/$file_name"
    local home_config_file="$HOME/$file_name"

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
        persist_config_file "$AGENT_CONFIG_FILE"
    fi
}
