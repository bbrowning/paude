#!/bin/bash
# Patch OpenClaw's web_fetch to route through the configured HTTP proxy.
#
# OpenClaw's fetchWithWebToolsNetworkGuard() creates a pinned dispatcher
# that bypasses proxy env vars. Passing useEnvProxy:true in the call
# options is the only way to make it respect HTTP_PROXY/HTTPS_PROXY.
#
# This script patches all occurrences so it adapts to filename changes
# (content-hashed bundles) across OpenClaw versions.
# It is idempotent, fail-safe, and skips patching when no proxy is set.
#
# Usage:
#   patch-proxy-fetch.sh          # Only patches if proxy env vars are set
#   patch-proxy-fetch.sh --force  # Patches unconditionally (for build time)

set -o pipefail

# Only patch when a proxy is actually configured (unless --force)
if [[ "${1:-}" != "--force" ]]; then
    proxy_url="${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}"
    if [[ -z "$proxy_url" ]]; then
        exit 0
    fi
fi

SEARCH_DIRS=(
    /app/dist
    /usr/lib/node_modules
    /usr/local/lib/node_modules
    "${HOME:+$HOME/.local/lib/node_modules}"
)

PATTERN='fetchWithWebToolsNetworkGuard({'
REPLACEMENT='fetchWithWebToolsNetworkGuard({useEnvProxy:true,'
patched=0

for dir in "${SEARCH_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue

    while IFS= read -r -d '' file; do
        # Skip files already patched
        if grep -q 'useEnvProxy:true' "$file" 2>/dev/null; then
            continue
        fi

        if sed -i "s|fetchWithWebToolsNetworkGuard({|fetchWithWebToolsNetworkGuard({useEnvProxy:true,|g" "$file" 2>/dev/null; then
            echo "[proxy-patch] Patched: $file" >&2
            patched=$((patched + 1))
        fi
    done < <(grep -rlZ "$PATTERN" "$dir" --include='*.js' 2>/dev/null)
done

if [[ $patched -eq 0 ]]; then
    echo "[proxy-patch] No files needed patching (upstream may have fixed this)" >&2
fi

exit 0
