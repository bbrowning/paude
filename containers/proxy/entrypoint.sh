#!/bin/bash
# Generate squid config in writable location (required for OpenShift random UIDs)
CONFIG_FILE=/tmp/squid.conf

# Copy base config to writable location
cp /etc/squid/squid.conf "$CONFIG_FILE"

# Inject DNS server if provided
if [[ -n "$SQUID_DNS" ]]; then
    echo "dns_nameservers $SQUID_DNS" >> "$CONFIG_FILE"
fi

# If ALLOWED_DOMAINS is set, replace the default allowed_domains ACL
# Format: comma-separated list of domains (e.g., ".googleapis.com,.google.com")
if [[ -n "$ALLOWED_DOMAINS" ]]; then
    # Remove existing allowed_domains ACL lines
    sed -i '/^acl allowed_domains dstdomain/d' "$CONFIG_FILE"

    # Parse and deduplicate domains
    # Squid treats .domain as matching domain AND *.domain, so if both
    # .example.com and example.com exist, keep only .example.com
    UNIQUE_DOMAINS=""
    IFS=',' read -ra DOMAINS <<< "$ALLOWED_DOMAINS"
    for domain in "${DOMAINS[@]}"; do
        domain=$(echo "$domain" | xargs)
        [[ -z "$domain" ]] && continue

        if [[ "$domain" == .* ]]; then
            # Wildcard domain - add it, and remove exact match if present
            exact="${domain:1}"
            # Remove exact match from list if present
            UNIQUE_DOMAINS=$(echo "$UNIQUE_DOMAINS" | sed "s/,${exact},/,/g; s/^${exact},//; s/,${exact}$//; s/^${exact}$//")
            # Add wildcard if not already present
            if ! echo ",$UNIQUE_DOMAINS," | grep -q ",${domain},"; then
                UNIQUE_DOMAINS="${UNIQUE_DOMAINS:+$UNIQUE_DOMAINS,}$domain"
            fi
        else
            # Exact domain - only add if wildcard doesn't exist
            wildcard=".$domain"
            if ! echo ",$UNIQUE_DOMAINS," | grep -q ",${wildcard},"; then
                # Also check it's not already in the list
                if ! echo ",$UNIQUE_DOMAINS," | grep -q ",${domain},"; then
                    UNIQUE_DOMAINS="${UNIQUE_DOMAINS:+$UNIQUE_DOMAINS,}$domain"
                fi
            fi
        fi
    done

    # Build new ACL entries
    NEW_ACLS=""
    IFS=',' read -ra FINAL_DOMAINS <<< "$UNIQUE_DOMAINS"
    for domain in "${FINAL_DOMAINS[@]}"; do
        [[ -n "$domain" ]] && NEW_ACLS="${NEW_ACLS}acl allowed_domains dstdomain $domain\n"
    done

    # Insert new ACLs before the SSL_ports ACL (must come before http_access rules)
    if [[ -n "$NEW_ACLS" ]]; then
        sed -i "s/^acl SSL_ports/${NEW_ACLS}acl SSL_ports/" "$CONFIG_FILE"
    fi
fi

# Run squid with the generated config
exec /usr/sbin/squid -f "$CONFIG_FILE" "$@"
