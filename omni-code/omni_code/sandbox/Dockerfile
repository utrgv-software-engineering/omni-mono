FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

ARG CODE_SERVER_VERSION=4.109.2

RUN apt-get update && apt-get install -y \
    ca-certificates \
    curl \
    git \
    sudo \
    python3 \
    python3-pip \
    python3-venv \
    pipx \
    nodejs \
    npm \
    ripgrep \
    fd-find \
    jq \
    sqlite3 \
    ffmpeg \
    pandoc \
    imagemagick \
    build-essential \
    pkg-config \
    libgit2-dev \
    gosu \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH="$(dpkg --print-architecture)" \
    && curl -fOL "https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server_${CODE_SERVER_VERSION}_${ARCH}.deb" \
    && apt-get update \
    && apt-get install -y "./code-server_${CODE_SERVER_VERSION}_${ARCH}.deb" \
    && rm -f "code-server_${CODE_SERVER_VERSION}_${ARCH}.deb" \
    && rm -rf /var/lib/apt/lists/*

ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

RUN pipx install omni-code --pip-args='--extra-index-url https://pypi.fury.io/ericmichael/'

COPY entrypoint.sh /usr/local/bin/omni-sandbox-entrypoint
RUN chmod +x /usr/local/bin/omni-sandbox-entrypoint

ENV OMNI_WEB_AUTO_OPEN=false
EXPOSE 7681
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/omni-sandbox-entrypoint"]
CMD ["omni", "--mode", "server", "--host", "0.0.0.0", "--port", "7681"]
