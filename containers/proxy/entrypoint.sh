#!/bin/bash
# Generate squid config in writable location (required for OpenShift random UIDs)
CONFIG_FILE=/tmp/squid.conf

# Copy base config to writable location
cp /etc/squid/squid.conf "$CONFIG_FILE"

# Inject DNS server if provided, always include public fallbacks
FALLBACK_DNS="8.8.8.8 1.1.1.1"
if [[ -n "$SQUID_DNS" ]]; then
    echo "dns_nameservers $SQUID_DNS $FALLBACK_DNS" >> "$CONFIG_FILE"
else
    echo "dns_nameservers $FALLBACK_DNS" >> "$CONFIG_FILE"
fi

# If ALLOWED_DOMAIN_ACLS is set (pre-formatted by Python), inject directly.
# ALLOWED_DOMAINS is kept for backward-compatible read-back (paude allow-domain list).
if [[ -n "${ALLOWED_DOMAIN_ACLS:-}" ]]; then
    # Remove existing allowed_domains ACL lines and related comments
    sed -i -e '/^acl allowed_domains\(_regex\)\? dst/d' \
           -e '/^# Regional endpoints/d' "$CONFIG_FILE"

    # Insert pre-formatted ACLs before the SSL_ports ACL
    sed -i "s/^acl SSL_ports/${ALLOWED_DOMAIN_ACLS}\nacl SSL_ports/" "$CONFIG_FILE"
fi

# If ALLOWED_OTEL_PORTS is set, inject port ACLs for OTEL endpoints
if [[ -n "${ALLOWED_OTEL_PORTS:-}" ]]; then
    IFS=',' read -ra PORTS <<< "$ALLOWED_OTEL_PORTS"
    for port in "${PORTS[@]}"; do
        port=$(echo "$port" | tr -d ' ')
        sed -i "/^acl Safe_ports port 443$/a acl Safe_ports port $port" "$CONFIG_FILE"
        sed -i "/^acl SSL_ports port 443$/a acl SSL_ports port $port" "$CONFIG_FILE"
    done
fi

# Validate config before starting (errors go to stderr for pod log visibility)
if ! /usr/sbin/squid -k parse -f "$CONFIG_FILE" 2>&1; then
    echo "ERROR: squid config validation failed. Generated config:" >&2
    cat -n "$CONFIG_FILE" >&2
    exit 1
fi

# Configure and start dnsmasq for DNS forwarding to main container
# This allows tools that resolve DNS locally (e.g. Rust reqwest) to work
# even on --internal networks where external DNS is unreachable.
DNSMASQ_CONF=/tmp/dnsmasq.conf
cat > "$DNSMASQ_CONF" <<DNSEOF
port=53
# Don't read /etc/resolv.conf or /etc/hosts — configure servers explicitly
no-resolv
no-hosts
DNSEOF

# Use the same upstream DNS servers as Squid
if [[ -n "$SQUID_DNS" ]]; then
    echo "server=$SQUID_DNS" >> "$DNSMASQ_CONF"
fi
for dns in $FALLBACK_DNS; do
    echo "server=$dns" >> "$DNSMASQ_CONF"
done

# Start dnsmasq as a background daemon
dnsmasq --conf-file="$DNSMASQ_CONF" --log-facility=/tmp/dnsmasq.log \
    || echo "WARNING: dnsmasq failed to start" >&2

# Clean up stale PID file from previous run (container restart)
rm -f /tmp/squid.pid

# Run squid under tini with process-group signaling (-g) so that
# SIGTERM reaches both squid and the background dnsmasq daemon.
exec tini -g -- /usr/sbin/squid -f "$CONFIG_FILE" "$@"
