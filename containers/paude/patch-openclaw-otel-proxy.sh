#!/bin/bash
# Patch OpenClaw's OTEL SDK to route HTTP exports through the HTTP proxy.
#
# The @opentelemetry/otlp-exporter-base package creates http/https Agents
# that do NOT respect HTTP_PROXY / HTTPS_PROXY env vars.  This script
# patches the compiled transport layer to return proxy-aware agents so
# OTEL exports go through the proxy.
#
# OpenClaw bundles its diagnostics-otel plugin into
# /app/dist/extensions/diagnostics-otel/index.js, so the primary patch
# target is that bundle.  The node_modules patches are kept as a fallback
# in case a future version stops bundling.
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
#
# The bundle runs as ESM ("type": "module" in package.json), where
# require() is not available.  We fall back to createRequire() via
# process.getBuiltinModule() (Node 22.3+).
read -r -d '' FACTORY_CODE << 'ENDOFCODE' || true
const __paudeProxyAgent = (() => { const p = process.env.HTTP_PROXY || process.env.http_proxy; if (!p) return null; try { let m; try { m = require; } catch(e) { m = process.getBuiltinModule("module").createRequire("/app/package.json"); } const { HttpProxyAgent } = m("http-proxy-agent"); const { HttpsProxyAgent } = m("https-proxy-agent"); return { create(url) { return String(url).startsWith("https") ? new HttpsProxyAgent(p) : new HttpProxyAgent(p); } }; } catch(e) { return null; } })();
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
                # Config layer: patch the httpAgentFactoryFromOptions function
                # definition to return a proxy-aware factory.  This covers all
                # call sites (default and environment) in one shot.
                if inject_factory "$file"; then
                    sed -i \
                        -e "s#function httpAgentFactoryFromOptions(\\([^)]*\\)) {#function httpAgentFactoryFromOptions(\\1) { if (${MARKER}) return async (url) => ${MARKER}.create(url);#" \
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

# === Part 2: Patch bundled dist file ===
# The stub at /app/dist/diagnostics-otel-*.js just re-exports; the real
# bundled code with all inlined OTEL logic lives here.
BUNDLE_FILE="/app/dist/extensions/diagnostics-otel/index.js"
if [[ -f "$BUNDLE_FILE" ]]; then
    if inject_factory "$BUNDLE_FILE"; then
        # Patch the httpAgentFactoryFromOptions function definition to
        # return a proxy-aware factory when __paudeProxyAgent is set.
        # This covers ALL call sites (default, environment, legacy)
        # regardless of whether they're single-line or multiline.
        sed -i \
            -e "s#function httpAgentFactoryFromOptions(\([^)]*\)) {#function httpAgentFactoryFromOptions(\1) { if (${MARKER}) return async (url) => ${MARKER}.create(url);#" \
            "$BUNDLE_FILE"
        echo "[otel-proxy-patch] Patched bundle: $BUNDLE_FILE" >&2
        patched=$((patched + 1))
    fi
fi

if [[ $patched -eq 0 ]]; then
    echo "[otel-proxy-patch] No files needed patching (OTEL files not found or already patched)" >&2
fi

exit 0
