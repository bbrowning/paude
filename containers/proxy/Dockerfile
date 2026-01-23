FROM quay.io/centos/centos:stream9

# Enable EPEL repository and install Squid + debugging tools
RUN dnf install -y epel-release && \
    dnf install -y --allowerasing squid curl wget bind-utils iputils && \
    dnf clean all

# Copy allowlist configuration and custom entrypoint
COPY squid.conf /etc/squid/squid.conf
COPY --chmod=755 entrypoint.sh /usr/local/bin/paude-entrypoint.sh

# Squid runs on port 3128
EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/paude-entrypoint.sh"]
CMD ["-f", "/etc/squid/squid.conf", "-NYC"]
