FROM docker.io/ubuntu/squid:latest

# Install debugging tools
RUN apt-get update && \
    apt-get install -y curl wget dnsutils iputils-ping && \
    rm -rf /var/lib/apt/lists/*

# Copy allowlist configuration and custom entrypoint
COPY squid.conf /etc/squid/squid.conf
COPY --chmod=755 entrypoint.sh /usr/local/bin/paude-entrypoint.sh

# Squid runs on port 3128
EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/paude-entrypoint.sh"]
CMD ["-f", "/etc/squid/squid.conf", "-NYC"]
