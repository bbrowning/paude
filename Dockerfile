FROM node:22-slim

RUN apt-get update && \
    apt-get install -y git curl wget dnsutils iputils-ping && \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

RUN useradd -m -s /bin/bash paude

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER paude
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
