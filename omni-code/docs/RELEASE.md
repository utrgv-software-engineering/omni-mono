# Release Process

## Prerequisites

- Install bump2version: `pip install bump2version`
- Install build: `pip install build`
- Set Fury.io credentials in `.env`:
  ```bash
  FURY_USER=your_username
  FURY_TOKEN=your_token
  ```

## Pre-flight Checks

- Ensure the working tree is clean:
  ```bash
  git status --porcelain=v1 --untracked-files=no
  ```
- Verify the bump configuration:
  ```bash
  test -f .bumpversion.cfg
  bump2version --dry-run --verbose patch
  ```

## Release Workflow

1. Commit all changes.
2. Bump the version with the desired target:
   - `make bump-patch`
   - `make bump-minor`
   - `make bump-major`
   This updates `pyproject.toml`, `omni_code/_version.py`, creates a commit, and tags `vX.Y.Z`.
3. Publish to Fury.io:
   ```bash
   make publish
   ```
   The Makefile builds wheels and sdists then uploads each artifact to your private index.
4. Push tags to GitHub if desired:
   ```bash
   git push origin main --tags
   ```

## Quick Patch Release

```bash
git status --porcelain=v1 --untracked-files=no
bump2version --dry-run --verbose patch
make bump-patch
make publish
git push origin main --tags
```

## Installing the Package

```bash
make install
```
or
```bash
pip install omni-code --index-url https://$FURY_TOKEN@pypi.fury.io/$FURY_USER/
```
