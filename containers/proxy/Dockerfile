FROM quay.io/centos/centos:stream10

# Enable CRB and EPEL repository, install Squid + dnsmasq + debugging tools
RUN dnf install -y dnf-plugins-core && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-release && \
    dnf install -y --allowerasing squid dnsmasq curl wget bind-utils iputils && \
    dnf clean all && \
    # Ensure squid runtime directories are writable (needed for OpenShift random UIDs)
    chmod -R 777 /var/spool/squid /var/log/squid /run/squid 2>/dev/null || true

# Install tini init process for multi-process signal handling
ARG TINI_VERSION=v0.19.0
RUN ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) TINI_ARCH="amd64" ;; \
        aarch64) TINI_ARCH="arm64" ;; \
        *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-${TINI_ARCH}" \
        -o /usr/local/bin/tini && \
    chmod +x /usr/local/bin/tini && \
    tini --version

# Copy allowlist configuration, custom error page, and entrypoint
COPY squid.conf /etc/squid/squid.conf
COPY ERR_CUSTOM_ACCESS_DENIED /usr/share/squid/errors/en/ERR_CUSTOM_ACCESS_DENIED
COPY --chmod=755 entrypoint.sh /usr/local/bin/paude-entrypoint.sh

# Squid runs on port 3128
EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/paude-entrypoint.sh"]
CMD ["-NYC"]
