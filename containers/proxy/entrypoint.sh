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

# If ALLOWED_DOMAINS is set, replace the default allowed_domains ACL
# Format: comma-separated list of domains (e.g., ".googleapis.com,.google.com")
if [[ -n "$ALLOWED_DOMAINS" ]]; then
    # Remove existing allowed_domains ACL lines (both dstdomain and dstdom_regex)
    sed -i '/^acl allowed_domains\(_regex\)\? dst/d' "$CONFIG_FILE"
    # Also remove comment lines immediately before regex ACLs
    sed -i '/^# Regional endpoints/d' "$CONFIG_FILE"

    # Parse and deduplicate domains
    # Squid treats .domain as matching domain AND *.domain, so if both
    # .example.com and example.com exist, keep only .example.com
    UNIQUE_DOMAINS=""
    IFS=',' read -ra DOMAINS <<< "$ALLOWED_DOMAINS"
    for domain in "${DOMAINS[@]}"; do
        domain=$(echo "$domain" | xargs)
        [[ -z "$domain" ]] && continue

        if [[ "$domain" == ~* ]]; then
            # Regex domain - pass through as-is, no dedup logic
            if ! echo ",$UNIQUE_DOMAINS," | grep -qF ",${domain},"; then
                UNIQUE_DOMAINS="${UNIQUE_DOMAINS:+$UNIQUE_DOMAINS,}$domain"
            fi
        elif [[ "$domain" == .* ]]; then
            # Wildcard domain - add it, and remove base domain + any subdomains
            # Squid treats .npmjs.org as matching npmjs.org AND *.npmjs.org,
            # so registry.npmjs.org is redundant and causes a fatal error.
            exact="${domain:1}"  # e.g. npmjs.org
            # Remove exact match and any subdomain (anything ending with the wildcard)
            NEW_UNIQUE=""
            IFS=',' read -ra EXISTING <<< "$UNIQUE_DOMAINS"
            for entry in "${EXISTING[@]}"; do
                [[ -z "$entry" ]] && continue
                # Skip if entry is the exact base domain or a subdomain
                if [[ "$entry" == "$exact" ]] || [[ "$entry" == *"$domain" ]]; then
                    continue
                fi
                NEW_UNIQUE="${NEW_UNIQUE:+$NEW_UNIQUE,}$entry"
            done
            UNIQUE_DOMAINS="$NEW_UNIQUE"
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
        [[ -z "$domain" ]] && continue
        if [[ "$domain" == ~* ]]; then
            # Regex domain: strip ~ prefix and use dstdom_regex
            regex="${domain:1}"
            NEW_ACLS="${NEW_ACLS}acl allowed_domains_regex dstdom_regex $regex\n"
        else
            NEW_ACLS="${NEW_ACLS}acl allowed_domains dstdomain $domain\n"
        fi
    done

    # Ensure allowed_domains_regex is always defined (squid errors on undefined ACLs)
    if ! echo -e "$NEW_ACLS" | grep -q 'allowed_domains_regex'; then
        NEW_ACLS="${NEW_ACLS}acl allowed_domains_regex dstdom_regex ^$\n"
    fi

    # Insert new ACLs before the SSL_ports ACL (must come before http_access rules)
    if [[ -n "$NEW_ACLS" ]]; then
        sed -i "s/^acl SSL_ports/${NEW_ACLS}acl SSL_ports/" "$CONFIG_FILE"
    fi
fi

# Validate config before starting (errors go to stderr for pod log visibility)
if ! /usr/sbin/squid -k parse -f "$CONFIG_FILE" 2>&1; then
    echo "ERROR: squid config validation failed. Generated config:" >&2
    cat -n "$CONFIG_FILE" >&2
    exit 1
fi

# Clean up stale PID file from previous run (container restart)
rm -f /tmp/squid.pid

# Run squid with the generated config
exec /usr/sbin/squid -f "$CONFIG_FILE" "$@"
