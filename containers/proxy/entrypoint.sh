#!/bin/bash
# Inject DNS server into squid config if provided via environment variable
if [[ -n "$SQUID_DNS" ]]; then
    echo "dns_nameservers $SQUID_DNS" >> /etc/squid/squid.conf
fi

# Run squid directly (UBI9 + EPEL squid package)
exec /usr/sbin/squid "$@"
