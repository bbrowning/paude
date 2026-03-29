#!/bin/bash
# Patch OpenClaw's OTEL SDK to route HTTP exports through the HTTP proxy.
#
# The @opentelemetry/otlp-exporter-base package creates http/https Agents
# that do NOT respect HTTP_PROXY / HTTPS_PROXY env vars.  This script
# patches the compiled transport layer to return proxy-aware agents so
# OTEL exports go through the squid proxy.
#
# OpenClaw ships the raw @opentelemetry packages in /app/node_modules
# (not bundled), so we patch the transport configuration directly.
#
# This script is idempotent, fail-safe, and skips patching when no proxy
# is set.
#
# Usage:
#   patch-openclaw-otel-proxy.sh          # Only patches if proxy env vars are set
#   patch-openclaw-otel-proxy.sh --force  # Patches unconditionally (for build time)

set -o pipefail

# Only patch when a proxy is actually configured (unless --force)
if [[ "${1:-}" != "--force" ]]; then
    proxy_url="${HTTPS_PROXY:-${HTTP_PROXY:-${https_proxy:-${http_proxy:-}}}}"
    if [[ -z "$proxy_url" ]]; then
        exit 0
    fi
fi

SEARCH_DIRS=(
    /app/node_modules
    /usr/lib/node_modules
    /usr/local/lib/node_modules
    "${HOME:+$HOME/.local/lib/node_modules}"
)

MARKER='__paudeProxyAgent'

# Proxy-aware agent factory injected at the top of patched files.
# Returns null when no proxy is set (short-circuits at module load),
# otherwise provides create(url) that picks HttpProxyAgent vs
# HttpsProxyAgent based on the target URL protocol.
#
# Both http-proxy-agent and https-proxy-agent are already installed in
# the OpenClaw image at /app/node_modules/.
read -r -d '' FACTORY_CODE << 'ENDOFCODE' || true
const __paudeProxyAgent = (() => { const p = process.env.HTTP_PROXY || process.env.http_proxy; if (!p) return null; try { const { HttpProxyAgent } = require("http-proxy-agent"); const { HttpsProxyAgent } = require("https-proxy-agent"); return { create(url) { try { return new URL(url).protocol === "https:" ? new HttpsProxyAgent(p) : new HttpProxyAgent(p); } catch(e) { return new HttpProxyAgent(p); } } }; } catch(e) { return null; } })();
ENDOFCODE

# Inject factory code and replace Agent constructors in a single file.
# $1 = file path, $2 = JS expression for the endpoint URL passed to create()
patch_otel_file() {
    local file="$1"
    local url_expr="$2"

    if grep -q "$MARKER" "$file" 2>/dev/null; then
        return 1
    fi

    # Inject factory after "use strict" or at the very top
    local strict_line
    strict_line=$(grep -n '"use strict"' "$file" | head -1 | cut -d: -f1)
    if [[ -n "$strict_line" ]]; then
        sed -i "${strict_line}a\\${FACTORY_CODE}" "$file"
    else
        sed -i "1i\\${FACTORY_CODE}" "$file"
    fi

    # Replace new http.Agent({...}) / new https.Agent({...}) with
    # proxy-aware alternatives that fall back to the original when
    # no proxy is configured.
    sed -i \
        -e "s|new http\\.Agent(\\({[^}]*}\\))|(${MARKER} ? ${MARKER}.create(${url_expr}) : new http.Agent(\\1))|g" \
        -e "s|new https\\.Agent(\\({[^}]*}\\))|(${MARKER} ? ${MARKER}.create(${url_expr}) : new https.Agent(\\1))|g" \
        "$file"

    return 0
}

patched=0

for dir in "${SEARCH_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue

    while IFS= read -r -d '' file; do
        case "$file" in
            */transport/http-exporter-transport.js)
                # Transport layer: use the instance's endpoint URL to pick agent type
                if patch_otel_file "$file" 'this._endpointUrl || this.url || ""'; then
                    echo "[otel-proxy-patch] Patched transport: $file" >&2
                    patched=$((patched + 1))
                fi
                ;;
            */configuration/otlp-node-http-configuration.js)
                # Config layer creates agents without an endpoint URL;
                # http.Agent → HttpProxyAgent, https.Agent → HttpsProxyAgent
                # (the protocol in the dummy URL just selects the right type)
                if patch_otel_file "$file" '"http:"'; then
                    echo "[otel-proxy-patch] Patched config: $file" >&2
                    patched=$((patched + 1))
                fi
                ;;
        esac
    done < <(find "$dir" \
        \( -path "*@opentelemetry/otlp-exporter-base/build/src/transport/http-exporter-transport.js" \
        -o -path "*@opentelemetry/otlp-exporter-base/build/src/configuration/otlp-node-http-configuration.js" \) \
        -print0 2>/dev/null)
done

if [[ $patched -eq 0 ]]; then
    echo "[otel-proxy-patch] No files needed patching (@opentelemetry/otlp-exporter-base not found or already patched)" >&2
fi

exit 0
