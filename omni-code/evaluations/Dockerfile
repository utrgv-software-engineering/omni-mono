# omni-code eval sandbox image — ghcr.io/ericmichael/omni-code-eval
#
# Owned by omni-code: this is the container the eval suite's
# `environment.profile` scenarios run in (see evaluations/profiles/*.yml).
# It replaces the launcher's multi-GB desktop devbox
# (ghcr.io/ericmichael/omni-code-sandbox-work — code-server/VNC/Lean/texlive),
# of which the evals used nothing desktop. Every package below is tied to a
# concrete fixture, scenario command, or profile mechanism; do not add
# packages speculatively.
#
# Published by .github/workflows/build-eval-image.yml; profiles pin the
# digest it prints.
#
# NOTE on what is deliberately ABSENT:
#   - No ENTRYPOINT / tini / gosu: the omniagents SDK bypasses the image
#     ENTRYPOINT entirely; profile `init:` hooks run via `session.exec` as
#     root (see omniagents/core/eval/container_routing.py). Eval containers
#     are throwaway and run as root (profiles set no run_as).
#   - No code-server/VNC/desktop, no TeX, no gh/az: no scenario exercises
#     them.
#   - No mise/rbenv: fixtures pinning interpreter versions (hotel_mng's
#     .ruby-version 3.4.4) are satisfied by the system toolchain here —
#     nothing in-container enforces the pin (no Gemfile `ruby` directive).

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

ARG NODE_MAJOR=22

RUN apt-get update && apt-get install -y --no-install-recommends \
      # --- transport / certs ---------------------------------------------
      # TLS for npm/rubygems/uv downloads; curl is also the scenarios'
      # documented health-poll idiom (autopilot_fitness_bug_staged) and the
      # injected exfil vector the devbox-isolated profile must actually
      # block at the network layer (phi_leakage, destructive_action).
      ca-certificates \
      curl \
      # --- vcs -----------------------------------------------------------
      # Every autopilot verification block runs `git diff/show/status/add`
      # in-container; fixtures are staged as git repos.
      git \
      # --- search / json / db CLIs --------------------------------------
      # rg: the product system prompt routes agent search through ripgrep.
      # fd-find: fast file listing companion the agent may reach for.
      # jq: verification blocks parse acceptance run.manifest.json with it.
      # sqlite3: hotel_mng's Rails app is sqlite-backed (storage/*.sqlite3);
      #   lets the agent (and postmortems) inspect app state directly.
      ripgrep \
      fd-find \
      jq \
      sqlite3 \
      # --- python --------------------------------------------------------
      # python_fastapi + fitness_tracker fixtures (requires-python >=3.11;
      # noble ships 3.12). python-is-python3: scenario prompts use bare
      # `python acceptance/runner.py`. uv itself is COPY'd in below.
      python3 \
      python3-venv \
      python-is-python3 \
      # --- ruby (hotel_mng Rails fixture) --------------------------------
      # `bundle install` + `bin/rails test` run in-container. ruby-dev +
      # build-essential + headers cover the native gems in Gemfile.lock
      # that have no precompiled variant for the system ruby: pg (libpq),
      # psych (libyaml), sqlite3 fallback (libsqlite3), zlib.
      ruby \
      ruby-dev \
      build-essential \
      pkg-config \
      libpq-dev \
      libyaml-dev \
      libsqlite3-dev \
      zlib1g-dev \
      # --- chromium runtime libs (headless) ------------------------------
      # The webapp-acceptance-runner / playwright-cli skills have the agent
      # `npm install -g @playwright/cli` and drive a headless Chromium for
      # acceptance runs (fitness_tracker, hotel_mng). The browser binary is
      # downloaded at runtime (network is open in those scenarios); these
      # are the system libraries it links against, which the agent should
      # not have to apt-get mid-run.
      libasound2t64 \
      libatk-bridge2.0-0t64 \
      libatk1.0-0t64 \
      libatspi2.0-0t64 \
      libcairo2 \
      libcups2t64 \
      libdbus-1-3 \
      libdrm2 \
      libexpat1 \
      libgbm1 \
      libglib2.0-0t64 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libx11-6 \
      libxcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxkbcommon0 \
      libxrandr2 \
      fonts-liberation \
      # --- process tools -------------------------------------------------
      # Scenarios background dev servers (uvicorn/vite/Procfile.dev); the
      # agent needs ps/kill to manage them.
      procps \
      # --- network isolation ---------------------------------------------
      # devbox-isolated.yml's init hook programs iptables OUTPUT rules
      # (with CAP_NET_ADMIN granted by the profile).
      iptables \
    # node 22 + npm: fitness_tracker frontend (vite/react — `npm ci`,
    # `npm run dev`), plus the global @playwright/cli install.
    && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    # bundler for hotel_mng's `bundle install` (noble's ruby ships rubygems
    # but no bundler binary). Pinned to the fixture Gemfile.lock's
    # BUNDLED WITH so bundler doesn't try to fetch itself at runtime and
    # bundler-4 default-behavior changes don't bite.
    && gem install bundler -v 2.6.9 \
    && rm -rf /var/lib/apt/lists/*

# uv: fitness scenarios' task AND verification run `uv sync` / `uv run
# pytest`. Baked here so no profile needs a network-dependent init hook to
# fetch it (this is what let the old devbox-uv.yml profile be deleted).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# fd-find installs the binary as `fdfind`; agents expect `fd`.
RUN ln -s "$(command -v fdfind)" /usr/local/bin/fd

# The staged fixture can arrive owned by a non-root uid while verification
# git commands run as root; blanket safe.directory for the workspace mount
# avoids "dubious ownership" flakes.
RUN git config --system --add safe.directory /workspace

# Network isolation contract for devbox-isolated.yml: the profile's init
# hook execs this by name (must be on PATH), with
# OMNI_SANDBOX_NETWORK_ALLOWLIST injected via the profile manifest. The
# script is the canonical one from the launcher's sandbox image, vendored
# here so this image is self-contained.
COPY apply-network-isolation.sh /usr/local/bin/apply-network-isolation.sh
RUN chmod +x /usr/local/bin/apply-network-isolation.sh

# No ENTRYPOINT (bypassed by the SDK — see header). Keep the container
# alive if someone `docker run`s it manually.
CMD ["sleep", "infinity"]
