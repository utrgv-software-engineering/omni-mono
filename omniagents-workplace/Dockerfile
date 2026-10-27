# The workplace: Slack, GitHub, Linear and canvases, self-hosted (omniagents-workplace).
# Built from omni-mono's root: docker build -f omniagents-workplace/Dockerfile .
# A factory's deployment (omni-factory's deploy/compose.yml) mounts its
# workplace.yml and the files its repositories start from.

# Its web UI, built with the npm workspace it lives in (only the packages it uses).
FROM node:22-bookworm-slim AS web
WORKDIR /src
COPY package.json package-lock.json ./
COPY patches patches
COPY scripts/harden-mcp-ui.cjs scripts/test-mcp-ui-security.cjs scripts/
COPY packages packages
COPY omniagents-workplace/web omniagents-workplace/web
# npm reads every workspace's package.json from the lockfile; the others are not built.
COPY omni-desktop/package.json omni-desktop/package.json
COPY omniagents/omniagents/backends/web/ui/package.json omniagents/omniagents/backends/web/ui/package.json
COPY omniagents/omniagents/backends/ink/tui/package.json omniagents/omniagents/backends/ink/tui/package.json
COPY omniagents-studio/web/package.json omniagents-studio/web/package.json
# The npm the lockfile is written with (npm 10 reads its overrides differently).
RUN npm install -g npm@11.6.2 --no-audit --no-fund
RUN npm ci --workspace omniagents-workplace/web --ignore-scripts --no-audit --no-fund
RUN node scripts/harden-mcp-ui.cjs && node scripts/test-mcp-ui-security.cjs
RUN npm run build --workspace omniagents-workplace/web

FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends git gh ca-certificates sqlite3 \
 && rm -rf /var/lib/apt/lists/*
# The docker CLI: workflow jobs run in containers of their own (--workflows docker).
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=ghcr.io/astral-sh/uv:0.9.22 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# The uv workspace: every member's pyproject, the workplace's sources.
COPY pyproject.toml uv.lock ./
COPY omniagents/pyproject.toml omniagents/README.md omniagents/
COPY omni-code/pyproject.toml omni-code/
COPY omniagents-studio/pyproject.toml omniagents-studio/
COPY omniagents-workplace omniagents-workplace
COPY --from=web /src/omniagents-workplace/web/dist omniagents-workplace/omniagents_workplace/static
RUN uv sync --frozen --no-dev --package omniagents-workplace --no-editable
ENV PATH=/app/.venv/bin:$PATH
EXPOSE 7300 7301
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:7300/healthz', timeout=4)" || exit 1
CMD ["omniagents-workplace", "--help"]
