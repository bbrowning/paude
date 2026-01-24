#!/bin/bash
# Inject DNS server into squid config if provided via environment variable
if [[ -n "$SQUID_DNS" ]]; then
    echo "dns_nameservers $SQUID_DNS" >> /etc/squid/squid.conf
fi

# If ALLOWED_DOMAINS is set, replace the default allowed_domains ACL
# Format: comma-separated list of domains (e.g., ".googleapis.com,.google.com")
if [[ -n "$ALLOWED_DOMAINS" ]]; then
    # Remove existing allowed_domains ACL lines
    sed -i '/^acl allowed_domains dstdomain/d' /etc/squid/squid.conf

    # Add new allowed_domains ACL entries
    IFS=',' read -ra DOMAINS <<< "$ALLOWED_DOMAINS"
    for domain in "${DOMAINS[@]}"; do
        # Trim whitespace
        domain=$(echo "$domain" | xargs)
        if [[ -n "$domain" ]]; then
            echo "acl allowed_domains dstdomain $domain" >> /etc/squid/squid.conf
        fi
    done
fi

# Run squid directly (UBI9 + EPEL squid package)
exec /usr/sbin/squid "$@"
