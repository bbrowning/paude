FROM quay.io/centos/centos:stream10

# Enable CRB and EPEL for additional packages
RUN dnf install -y dnf-plugins-core && \
    dnf config-manager --set-enabled crb && \
    dnf install -y epel-release && \
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
        python3.12 \
        python3.12-pip \
        procps-ng \
        tmux \
        which \
        coreutils \
        findutils \
        grep \
        sed \
        gawk \
        diffutils \
        less \
        file \
        tar \
        gzip \
        xz \
        unzip \
        zip \
        tree \
    && dnf clean all && \
    alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# Set UTF-8 locale
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

# Install uv package manager system-wide (available to root for pip_install builds)
RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

# Install pre-commit for automatic hook support in git-synced repos
RUN pip3.12 install pre-commit

# Install GitHub CLI (gh)
RUN curl -fsSL https://cli.github.com/packages/rpm/gh-cli.repo -o /etc/yum.repos.d/gh-cli.repo && \
    dnf install -y gh && \
    dnf clean all

# Create non-root user with GID 0 for OpenShift compatibility
RUN useradd -M -d /home/paude -s /bin/bash -g 0 paude && \
    umask 0002 && \
    mkdir -p /home/paude/.claude /home/paude/.config && \
    chown -R paude:0 /home/paude

# NOTE: Claude Code is NOT installed here due to licensing restrictions.
# It gets installed at user-side build time via a runtime layer.
# See src/paude/config/claude_layer.py for the installation logic.

# Copy entrypoints and watchdog script
USER root
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh
COPY --chmod=755 entrypoint-session.sh /usr/local/bin/entrypoint-session.sh
COPY --chmod=755 credential-watchdog.sh /usr/local/bin/credential-watchdog.sh
COPY --chmod=664 tmux.conf /home/paude/.tmux.conf

USER paude
WORKDIR /home/paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
