FROM quay.io/centos/centos:stream9

# Enable EPEL for additional packages
RUN dnf install -y epel-release && \
    dnf install -y --allowerasing \
        git \
        curl \
        wget \
        rsync \
        bind-utils \
        iputils \
        jq \
        perl \
        make \
        ca-certificates \
        glibc-langpack-en \
        python3.11 \
        python3.11-pip \
        tmux \
    && dnf clean all && \
    alternatives --install /usr/bin/python python /usr/bin/python3.11 1 && \
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Set UTF-8 locale
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install uv package manager system-wide (available to root for pip_install builds)
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

# Create non-root user with GID 0 for OpenShift compatibility
RUN useradd -m -s /bin/bash -g 0 paude && \
    chmod -R g+rwX /home/paude && \
    mkdir -p /home/paude/.claude /home/paude/.config && \
    chmod -R g+rwX /home/paude/.claude /home/paude/.config

# NOTE: Claude Code is NOT installed here due to licensing restrictions.
# It gets installed at user-side build time via a runtime layer.
# See src/paude/config/claude_layer.py for the installation logic.

# Copy entrypoints
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=755 entrypoint-session.sh /usr/local/bin/entrypoint-session.sh

RUN chmod -R g+rwX /home/paude

USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
