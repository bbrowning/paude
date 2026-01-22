FROM debian:bookworm-slim

# System dependencies
RUN apt-get update && \
    apt-get install -y \
        git \
        curl \
        wget \
        dnsutils \
        iputils-ping \
        jq \
        perl \
        make \
        ca-certificates \
        bash \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash paude

# Install Claude Code using native installer (as paude user)
USER paude
WORKDIR /home/paude
RUN curl -fsSL https://claude.ai/install.sh | bash

# Disable auto-updates (version controlled by image rebuild)
ENV DISABLE_AUTOUPDATER=1

# Ensure claude is in PATH
ENV PATH="/home/paude/.local/bin:$PATH"

# Copy entrypoint (requires root, then switch back)
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Run as non-root user
USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
