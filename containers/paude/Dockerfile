FROM debian:bookworm-slim

# System dependencies including Python for venv creation
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
        tmux \
        locales \
        python3 \
        python3-venv \
        python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8

# Set UTF-8 locale for proper terminal rendering
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Create non-root user with root group (GID 0) for OpenShift compatibility
# OpenShift runs containers with arbitrary UIDs but GID 0, so home must be group-writable
RUN useradd -m -s /bin/bash -g 0 paude && \
    chmod -R g+rwX /home/paude && \
    mkdir -p /home/paude/.claude /home/paude/.config && \
    chmod -R g+rwX /home/paude/.claude /home/paude/.config

# Install Claude Code using native installer (as paude user)
USER paude
WORKDIR /home/paude
RUN curl -fsSL https://claude.ai/install.sh | bash

# Install uv for fast Python package management at runtime
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Disable auto-updates (version controlled by image rebuild)
ENV DISABLE_AUTOUPDATER=1

# Ensure claude is in PATH
ENV PATH="/home/paude/.local/bin:$PATH"

# Copy entrypoints (requires root, then switch back)
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=755 entrypoint-tmux.sh /usr/local/bin/entrypoint-tmux.sh
COPY --chmod=755 entrypoint-session.sh /usr/local/bin/entrypoint-session.sh

# Fix permissions on directories created by Claude install
# Must be done as root after Claude install, for OpenShift arbitrary UID
RUN chmod -R g+rwX /home/paude

# Run as non-root user
USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
