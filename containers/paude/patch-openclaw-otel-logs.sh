#!/bin/bash
# Patch OpenClaw's log transport to survive dual module loading (jiti).
#
# When diagnostics-otel loads as a plugin via jiti, it gets a separate
# module instance of logger-*.js with its own externalTransports Set
# and loggingState.  Transports registered by the plugin are invisible
# to the gateway's logger, so OTEL logs never flow.
#
# Fix: promote externalTransports and activeLogger to a globalThis
# singleton so both module instances share the same state.
# Mirrors openclaw/openclaw#50085.
#
# This script is idempotent and fail-safe.

set -o pipefail

MARKER='__openclawLogTransportState'

# Find the logger module (filename includes a content hash)
LOGGER_FILE=""
for f in /app/dist/logger-*.js; do
    [[ -f "$f" ]] && LOGGER_FILE="$f" && break
done

if [[ -z "$LOGGER_FILE" ]]; then
    echo "[otel-log-patch] No logger-*.js found in /app/dist/" >&2
    exit 0
fi

if grep -q "$MARKER" "$LOGGER_FILE"; then
    echo "[otel-log-patch] Already patched: $LOGGER_FILE" >&2
    exit 0
fi

# Apply the patch using Node.js for reliable string/regex replacements.
node -e '
const fs = require("fs");
const file = process.argv[1];
let code = fs.readFileSync(file, "utf8");
let changes = 0;

// 1. Share externalTransports Set via globalThis singleton.
//    Both the gateway and jiti-loaded plugin will reference the same Set.
// Match with or without the /* @__PURE__ */ annotation (bundler-dependent).
const old1 = /const externalTransports = (\/\* @__PURE__ \*\/ )?new Set\(\);/;
const new1 = "const __ocLTS = globalThis.__openclawLogTransportState || " +
    "(globalThis.__openclawLogTransportState = { transports: new Set(), activeLogger: null }); " +
    "const externalTransports = __ocLTS.transports;";
let prev = code;
code = code.replace(old1, new1);
if (code !== prev) changes++;

// 2. Publish activeLogger after transport attachment in buildLogger.
//    This lets late-registering plugins attach to the live logger.
const old2 = "for (const transport of externalTransports) attachExternalTransport(logger, transport);";
const new2 = old2 + " if (globalThis.__openclawLogTransportState) globalThis.__openclawLogTransportState.activeLogger = logger;";
prev = code;
code = code.replaceAll(old2, new2);
if (code !== prev) changes++;

// 3. In registerLogTransport, use globalThis activeLogger instead of
//    loggingState.cachedLogger (which is null in the jiti module instance).
const old3 = /externalTransports\.add\(transport\);(\s+)const logger = loggingState\.cachedLogger;/;
if (old3.test(code)) {
    code = code.replace(old3,
        "externalTransports.add(transport);$1" +
        "const logger = (globalThis.__openclawLogTransportState && " +
        "globalThis.__openclawLogTransportState.activeLogger) || loggingState.cachedLogger;");
    changes++;
}

// 4. Clear activeLogger when logger is reset or overridden.
const old4 = "loggingState.cachedLogger = null;";
const new4 = old4 + " if (globalThis.__openclawLogTransportState) globalThis.__openclawLogTransportState.activeLogger = null;";
prev = code;
code = code.replaceAll(old4, new4);
if (code !== prev) changes++;

if (changes > 0) {
    fs.writeFileSync(file, code);
    process.stderr.write("[otel-log-patch] Applied " + changes + " transformation(s) to " + file + "\n");
} else {
    process.stderr.write("[otel-log-patch] WARNING: no patterns matched in " + file + "\n");
    process.exit(1);
}
' "$LOGGER_FILE"

exit 0
