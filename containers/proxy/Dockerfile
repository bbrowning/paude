# Build stage — compile paude-proxy from source
FROM golang:1.23 AS builder
WORKDIR /app
ARG PAUDE_PROXY_VERSION=c2be1d01b0eafad90fb2d2ee50f72a2bb9f6f4f6
RUN git init . && \
    git fetch --depth 1 https://github.com/bbrowning/paude-proxy.git ${PAUDE_PROXY_VERSION} && \
    git checkout FETCH_HEAD && \
    CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /paude-proxy ./cmd/paude-proxy/

# Runtime stage
FROM quay.io/centos/centos:stream10

# dnsmasq for DNS forwarding (needed by tools like Rust reqwest on internal networks)
# curl for health checks and debugging
RUN dnf install -y dnsmasq curl && dnf clean all

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

COPY --from=builder /paude-proxy /usr/local/bin/paude-proxy
COPY --chmod=755 entrypoint.sh /usr/local/bin/paude-entrypoint.sh

# Writable directories for OpenShift arbitrary UIDs
RUN mkdir -p /data/ca /tmp && chmod 777 /data/ca /tmp
# dnsmasq needs writable pid directory
RUN mkdir -p /run/dnsmasq && chmod 777 /run/dnsmasq

EXPOSE 3128

ENTRYPOINT ["/usr/local/bin/tini", "--", "/usr/local/bin/paude-entrypoint.sh"]
