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

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
      # --- transport / certs ---------------------------------------------
      # TLS for uv downloads; curl is also the injected exfil vector the devbox-isolated profile must actually
      # block at the network layer (phi_leakage, destructive_action).
      ca-certificates \
      curl \
      # --- vcs -----------------------------------------------------------
      # Fixtures are staged as git repos.
      git \
      # --- search CLIs ---------------------------------------------------
      # rg: the product system prompt routes agent search through ripgrep.
      # fd-find: fast file listing companion the agent may reach for.
      ripgrep \
      fd-find \
      # --- python --------------------------------------------------------
      # python_fastapi fixture (requires-python >=3.11; noble ships 3.12).
      # uv itself is COPY'd in below.
      python3 \
      python3-venv \
      python-is-python3 \
      # --- process tools -------------------------------------------------
      # Scenarios background dev servers (uvicorn); the agent needs
      # ps/kill to manage them.
      procps \
      # --- network isolation ---------------------------------------------
      # devbox-isolated.yml's init hook programs iptables OUTPUT rules
      # (with CAP_NET_ADMIN granted by the profile).
      iptables \
    && rm -rf /var/lib/apt/lists/*

# uv: python_fastapi scenarios run `uv sync` / `uv run pytest`. Baked here so no profile needs a network-dependent init hook to
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
