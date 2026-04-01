#!/usr/bin/env bash
set -euo pipefail

# ── DNS Configuration ────────────────────────────────────────────────
# Start dnsmasq for local DNS forwarding. This is required for tools
# that bypass the system resolver (e.g., Rust reqwest) and need a DNS
# server on localhost.

DNSMASQ_CONF="/tmp/dnsmasq.conf"

# Build dnsmasq config from resolv.conf
{
    echo "# Auto-generated dnsmasq config"
    echo "listen-address=127.0.0.1"
    echo "port=53"
    echo "bind-interfaces"
    echo "no-resolv"
    echo "no-poll"
    echo "no-daemon"

    # Forward to upstream DNS servers from resolv.conf
    if [[ -f /etc/resolv.conf ]]; then
        while IFS= read -r line; do
            if [[ "$line" =~ ^nameserver[[:space:]]+(.+) ]]; then
                server="${BASH_REMATCH[1]}"
                # Skip localhost (that would be us)
                if [[ "$server" != "127.0.0.1" && "$server" != "::1" ]]; then
                    echo "server=$server"
                fi
            fi
        done < /etc/resolv.conf
    fi

    # Use custom DNS if provided (backward compat with SQUID_DNS env var)
    if [[ -n "${SQUID_DNS:-}" ]]; then
        echo "server=$SQUID_DNS"
    fi

    # Fallback public DNS
    echo "server=8.8.8.8"
    echo "server=1.1.1.1"
} > "$DNSMASQ_CONF"

echo "Starting dnsmasq..."
dnsmasq --conf-file="$DNSMASQ_CONF" &
DNSMASQ_PID=$!

# Give dnsmasq a moment to start
sleep 0.2

# ── Paude Proxy ─────────────────────────────────────────────────────
echo "Starting paude-proxy..."
exec /usr/local/bin/paude-proxy
