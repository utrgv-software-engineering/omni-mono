# CLAUDE.md

Guidance for working in omni-mono. Each package has its own `CLAUDE.md` for
its architecture and commands: `omniagents/`, `omni-code/`, `omni-desktop/`.

## Layout

- `omniagents/`: agent framework (`omniagents` on Gemfury)
- `omni-code/`: the Omni Code product, built on omniagents (`omni-code` on Gemfury)
- `omni-desktop/`: Electron + browser app that installs and runs Omni Code

omni-code depends on an exact `omniagents[all]==` pin, and omni-desktop
installs an exact omni-code `pinnedVersion` (`omni-desktop/src/lib/product.ts`).
A change that spans packages lands in one commit.

## Python development

The root `pyproject.toml` is a uv workspace for omniagents and omni-code:

```bash
uv sync --all-packages --all-groups --extra dev --extra server   # .venv at the root
```

omni-code then runs against this repository's omniagents. Run tools from a
package directory (`cd omniagents && ../.venv/bin/mypy`). Don't run Python
from the repository root: the `omniagents/` folder there shadows the package.

## CI

Workflows live in `.github/workflows/`, named `<package>-<name>.yml`. Each
runs inside its package directory (`defaults.run.working-directory`) and only
for changes under that package. omni-code's quality gates also run on
omniagents changes, because they test against it. `pages.yml` publishes one
docs site: `/omniagents/` (MkDocs) and `/omni-desktop/` (`omni-desktop/docs`).

## Branches and releases

Work happens on `dev`. `main` holds released code only. Tags are prefixed per
package: `omniagents-v*`, `omni-code-v*`, `omni-desktop-v*`. Pushing to `main`
does not publish anything; each release is its own tag.

`make publish` in the Python packages refuses to run anywhere but a clean
`main` in sync with `origin/main`, and uploads to Gemfury (`FURY_USER`,
`FURY_TOKEN`).

Release train, in dependency order (skip packages that are not changing):

1. `git switch main && git merge dev`
2. **omniagents**: `cd omniagents && make bump-patch` (commits and tags
   `omniagents-v<version>`), `git push --follow-tags`, `make publish`.
3. **omni-code**: set the `omniagents[all]==` pin in `omni-code/pyproject.toml`
   to the new version and commit; `cd omni-code && make bump-patch`,
   `git push --follow-tags`, `make publish`.
4. **omni-desktop**: set `pinnedVersion` in `src/lib/product.ts` (and
   `product.test.ts`), bump `package.json`'s version, commit, then
   `git tag omni-desktop-v<version> && git push origin main omni-desktop-v<version>`.
   `omni-desktop-build.yml` builds, smoke-tests and publishes the release to
   `ericmichael/omni-desktop`, which installed apps use as their update feed.
   That needs the `OMNI_DESKTOP_RELEASE_TOKEN` secret (contents: write on that
   repository). The tag must match `package.json`.
5. `git switch dev && git merge main`.

To build and smoke-test installers without publishing, dispatch
`omni-desktop-build.yml` (`gh workflow run omni-desktop-build.yml --ref dev`).

Hotfix for a released bug: branch from `main`, fix, release as above, then
merge `main` into `dev`.
