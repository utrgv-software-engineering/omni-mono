# Release Process

## Prerequisites

- Install bump2version: `pip install bump2version`
- Install build: `pip install build`
- Set up your Fury.io credentials in `.env`:
  ```
  FURY_USER=your_username
  FURY_TOKEN=your_token
  ```

## Pre-flight Checks

- Ensure there are no staged or modified files (untracked files are fine and will not be committed):
  ```bash
  git status --porcelain=v1 --untracked-files=no  # should print nothing
  ```
- Confirm the versioning config is present and recognized (do not edit it):
  ```bash
  test -f .bumpversion.cfg
  bump2version --dry-run --verbose patch  # should show new_version and list files it would change
  ```
  Note: `.bumpversion.cfg` is the source of truth. Do not create or modify it manually.

## Release Workflow

1. **Ensure all changes are committed**
   ```bash
   git status  # Should show clean working directory
   git add .
   git commit -m "Description of changes"
   ```

2. **Bump the version**
   
   Choose the appropriate version bump:
   - `make bump-patch` - Bug fixes (0.2.0 → 0.2.1)
   - `make bump-minor` - New features (0.2.0 → 0.3.0)
   - `make bump-major` - Breaking changes (0.2.0 → 1.0.0)
   
   This automatically:
   - Updates version in `omniagents/_version.py`
   - Updates the `.bumpversion.cfg` current_version
   - Creates a git commit with message "Bump version: X.Y.Z → X.Y.Z"
   - Creates a git tag `vX.Y.Z`

3. **Publish to Fury.io**
   ```bash
   make publish
   ```
   
   This will:
   - Clean previous builds
   - Build source distribution and wheel using `python -m build`
   - Upload to your Fury.io repository

4. **Push to GitHub** (optional)
   ```bash
   git push origin main --tags
   ```

## Standard Patch Release (Quick Path)

```bash
git status --porcelain=v1 --untracked-files=no
bump2version --dry-run --verbose patch
make bump-patch
make publish
git push origin main --tags
```

## Installing the Package

Once published, install your package with:
```bash
make install
```

Or directly:
```bash
pip install omniagents --index-url https://YOUR_TOKEN@pypi.fury.io/YOUR_USER/
```

## Version History

Check version tags:
```bash
git tag -l
```

View current version:
```bash
python -c "from omniagents._version import __version__; print(__version__)"
```
