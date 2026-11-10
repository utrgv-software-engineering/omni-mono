# Optional custom act job image. The default uses this upstream image directly.
# Build from omni-mono's root with -f omniagents-workplace/runner.Dockerfile.
FROM ghcr.io/catthehacker/ubuntu:act-22.04
RUN npm install -g yarn@1.22.22 && apt-get update -qq
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv
