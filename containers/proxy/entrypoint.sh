#!/bin/bash
# Inject DNS server into squid config if provided via environment variable
if [[ -n "$SQUID_DNS" ]]; then
    echo "dns_nameservers $SQUID_DNS" >> /etc/squid/squid.conf
fi

# Execute the original ubuntu/squid entrypoint
exec /usr/local/bin/entrypoint.sh "$@"
