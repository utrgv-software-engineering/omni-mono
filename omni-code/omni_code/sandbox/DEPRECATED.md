# Deprecated: sandbox image moved to the launcher repo

The Dockerfile + entrypoint + helper scripts in this directory have been
copied to the launcher repository at `launcher/sandbox-image/`. The
launcher is now the source of truth for the **devbox** sandbox image
under the v22 cut (Shape B — agent runs on the host, container is a
generic dev environment driven via `SandboxSession`).

These files are kept here temporarily for backward compatibility with:

* Existing `omni sandbox --dockerfile <path>` invocations
* The published `ghcr.io/ericmichael/omni-code-sandbox-work:latest` tag
  that the launcher's bundled `devbox.yml` profile still references

Once the launcher CI publishes images from `launcher/sandbox-image/` and
`devbox.yml` points at the launcher-published tag, this directory and
the `omni sandbox` CLI subcommand can be removed entirely.

Until then, **edit the launcher copy, not this one** — keeping the two
in lockstep is not worth the friction.
