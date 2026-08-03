FROM node:22.18.0-bookworm-slim

ARG CODEX_VERSION=0.146.0

RUN apt-get update \
    && apt-get install --yes --no-install-recommends bubblewrap ca-certificates git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@openai/codex@${CODEX_VERSION}" \
    && find /usr/local/lib/node_modules/@openai/codex \
        -type f -path "*/codex-resources/bwrap" -delete \
    && npm cache clean --force

RUN groupadd --gid 10001 codex \
    && useradd --create-home --gid codex --uid 10001 codex

USER codex
ENV HOME=/home/codex
ENV CODEX_HOME=/home/codex/.codex
WORKDIR /workspace

ENTRYPOINT ["codex"]
