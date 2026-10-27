# Release

Desktop releases follow the release train in the repository root `CLAUDE.md`:
set `pinnedVersion` in `src/lib/product.ts` (and `product.test.ts`), bump
`package.json`'s version, commit on `main`, then push an
`omni-desktop-v<version>` tag. `omni-desktop-build.yml` builds, smoke-tests and
publishes the release to `ericmichael/omni-desktop`, which installed apps use as
their update feed.

## Auto-Updating Launcher

The launcher prompts the user when a new version is available. The user can
update now or later; updating now downloads the new version and restarts the
app.
