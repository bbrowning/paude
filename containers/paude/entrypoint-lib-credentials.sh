#!/bin/bash
# Credential and path waiting utilities for the paude entrypoint.
# Sourced by entrypoint-session.sh — not run standalone.

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

# Detect the system CA bundle path across distros.
_find_sys_ca_bundle() {
    local path
    for path in \
        /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem \
        /etc/ssl/certs/ca-certificates.crt \
        /etc/ssl/ca-bundle.pem \
        /etc/ssl/cert.pem; do
        if [[ -f "$path" ]]; then
            echo "$path"
            return 0
        fi
    done
    return 1
}

# Build custom CA bundle with paude-proxy CA cert if injected.
# Concatenates the system CA bundle with the proxy CA cert into a
# writable /tmp path — no root or update-ca-trust needed (works with
# OpenShift arbitrary UIDs and non-CentOS base images like Debian).
setup_ca_trust() {
    local ca_cert="/etc/pki/ca-trust/source/anchors/paude-proxy-ca.crt"
    local custom_bundle="/tmp/paude-ca-bundle.pem"
    local sys_bundle
    sys_bundle=$(_find_sys_ca_bundle) || return 0

    if [[ -f "$ca_cert" ]]; then
        cat "$sys_bundle" "$ca_cert" > "$custom_bundle" 2>/dev/null || true
    else
        cp "$sys_bundle" "$custom_bundle" 2>/dev/null || true
    fi
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
