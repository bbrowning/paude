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

# Inject the factory code into a file (after "use strict" or at the top).
# Returns 1 if already patched.
inject_factory() {
    local file="$1"

    if grep -q "$MARKER" "$file" 2>/dev/null; then
        return 1
    fi

    local strict_line
    strict_line=$(grep -n '"use strict"' "$file" | head -1 | cut -d: -f1)
    if [[ -n "$strict_line" ]]; then
        sed -i "${strict_line}a\\${FACTORY_CODE}" "$file"
    else
        sed -i "1i\\${FACTORY_CODE}" "$file"
    fi

    return 0
}

patched=0

for dir in "${SEARCH_DIRS[@]}"; do
    [[ -d "$dir" ]] || continue

    while IFS= read -r -d '' file; do
        case "$file" in
            */transport/http-exporter-transport.js)
                # Transport layer: replace new http.Agent / new https.Agent
                # with proxy-aware agents using the instance's endpoint URL.
                if inject_factory "$file"; then
                    sed -i \
                        -e "s#new http\\.Agent(\\({[^}]*}\\))#(${MARKER} ? ${MARKER}.create(this._endpointUrl || this.url || \"\") : new http.Agent(\\1))#g" \
                        -e "s#new https\\.Agent(\\({[^}]*}\\))#(${MARKER} ? ${MARKER}.create(this._endpointUrl || this.url || \"\") : new https.Agent(\\1))#g" \
                        "$file"
                    echo "[otel-proxy-patch] Patched transport: $file" >&2
                    patched=$((patched + 1))
                fi
                ;;
            */configuration/otlp-node-http-configuration.js)
                # Config layer: the default agentFactory is set via
                #   agentFactory: httpAgentFactoryFromOptions({ keepAlive: true })
                # Replace it so the proxy agent is used when HTTP_PROXY is set.
                if inject_factory "$file"; then
                    sed -i \
                        -e "s#httpAgentFactoryFromOptions(\\({[^}]*}\\))#(${MARKER} ? (url => ${MARKER}.create(url)) : httpAgentFactoryFromOptions(\\1))#g" \
                        "$file"
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
