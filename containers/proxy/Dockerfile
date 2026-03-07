FROM quay.io/centos/centos:stream9

# Enable EPEL repository and install Squid + debugging tools
RUN dnf install -y epel-release && \
    dnf install -y --allowerasing squid curl wget bind-utils iputils && \
    dnf clean all && \
    # Ensure squid runtime directories are writable (needed for OpenShift random UIDs)
    chmod -R 777 /var/spool/squid /var/log/squid /run/squid 2>/dev/null || true

# Copy allowlist configuration, custom error page, and entrypoint
COPY squid.conf /etc/squid/squid.conf
COPY ERR_CUSTOM_ACCESS_DENIED /usr/share/squid/errors/en/ERR_CUSTOM_ACCESS_DENIED
COPY --chmod=755 entrypoint.sh /usr/local/bin/paude-entrypoint.sh

# Squid runs on port 3128
EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/paude-entrypoint.sh"]
CMD ["-NYC"]
