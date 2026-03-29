#!/bin/bash
# Patch Gemini CLI's OTEL SDK to route HTTP exports through the HTTP proxy.
#
# The @opentelemetry/exporter-*-otlp-http packages use Node.js http.request
# which does NOT respect HTTP_PROXY / HTTPS_PROXY env vars.  This script
# patches the compiled sdk.js to pass an httpAgentOptions factory that
# returns an HttpsProxyAgent (already a gemini-cli dependency) so OTEL
# exports go through the squid proxy.
#
# The OTEL SDK v0.211.0 (used by gemini-cli v0.35.3) supports
# httpAgentOptions as either AgentOptions or an HttpAgentFactory function.
#
# This script is idempotent, fail-safe, and skips patching when no proxy
# is set.
#
# Usage:
#   patch-gemini-otel-proxy.sh          # Only patches if proxy env vars are set
#   patch-gemini-otel-proxy.sh --force  # Patches unconditionally (for build time)

set -o pipefail

# Only patch when a proxy is actually configured (unless --force)
if [[ "${1:-}" != "--force" ]]; then
    proxy_url="${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}"
    if [[ -z "$proxy_url" ]]; then
        exit 0
    fi
fi

SEARCH_DIRS=(
    /usr/lib/node_modules
    /usr/local/lib/node_modules
    "${HOME:+$HOME/.local/lib/node_modules}"
)

MARKER='__paudeOtelProxyFactory'

# Returns { keepAlive: true } when no proxy is set (OTEL default), otherwise
# returns an async HttpAgentFactory that creates HttpsProxyAgent instances.
FACTORY_CODE='const __paudeOtelProxyFactory = (() => { const p = process.env.HTTP_PROXY || process.env.http_proxy; if (!p) return { keepAlive: true }; return async () => { const { HttpsProxyAgent } = await import("https-proxy-agent"); return new HttpsProxyAgent(p); }; })();'

patched=0

for dir in "${SEARCH_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue

    while IFS= read -r -d '' file; do
        if grep -q "$MARKER" "$file" 2>/dev/null; then
            continue
        fi

        if ! grep -q "buildUrl('v1/traces')" "$file" 2>/dev/null && \
           ! grep -q 'buildUrl("v1/traces")' "$file" 2>/dev/null; then
            continue
        fi

        last_import_line=$(grep -n '^import ' "$file" | tail -1 | cut -d: -f1)
        if [[ -z "$last_import_line" ]]; then
            echo "[otel-proxy-patch] No import statements found in $file, skipping" >&2
            continue
        fi

        # Inject factory code after last import, then add httpAgentOptions to
        # each OTLP exporter constructor (handle both quote styles from tsc).
        sed_args=(-e "${last_import_line}a\\${FACTORY_CODE}")
        for endpoint in traces logs metrics; do
            sed_args+=(-e "s|buildUrl('v1/$endpoint')|buildUrl('v1/$endpoint'), httpAgentOptions: __paudeOtelProxyFactory|")
            sed_args+=(-e "s|buildUrl(\"v1/$endpoint\")|buildUrl(\"v1/$endpoint\"), httpAgentOptions: __paudeOtelProxyFactory|")
        done
        sed -i "${sed_args[@]}" "$file"

        echo "[otel-proxy-patch] Patched: $file" >&2
        patched=$((patched + 1))
    done < <(find "$dir" -path "*gemini-cli-core/dist/*/telemetry/sdk.js" -print0 2>/dev/null)
done

if [[ $patched -eq 0 ]]; then
    echo "[otel-proxy-patch] No files needed patching (gemini-cli-core not found or already patched)" >&2
fi

exit 0
