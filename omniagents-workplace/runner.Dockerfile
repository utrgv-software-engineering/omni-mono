# What a workflow job runs in (the workplace's --workflow-image): GitHub's
# ubuntu-latest, simplified to what a factory's repositories use: git, bash,
# Python 3 (with venv and pip, for actions/setup-python), uv, and Node.js.
# Built from omni-mono's root: docker build -f omniagents-workplace/runner.Dockerfile .
FROM node:22-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-venv python3-pip ripgrep \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv
WORKDIR /runner
