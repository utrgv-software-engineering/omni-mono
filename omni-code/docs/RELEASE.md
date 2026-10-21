# Release Process

## Prerequisites

- `pip install bump2version build`
- A `.env` at the repo root with Fury.io credentials — the Makefile
  does `include .env`, so it must exist for any `make` target:
  ```bash
  FURY_USER=your_username
  FURY_TOKEN=your_token
  ```

## What a version bump touches

`bump2version` (config: `.bumpversion.cfg`) rewrites exactly two files —
`pyproject.toml` and `omni_code/_version.py` — then commits
(`Bump version: X → Y`) and tags `vX.Y.Z`. Nothing else is generated or
edited automatically; there is no changelog step.

## Release Workflow

1. **Advance the omniagents pin if the framework moved.** omni-code
   releases usually track a framework release: publish omniagents to
   Fury first, then update the pin in `pyproject.toml`
   (`omniagents[all]==X.Y.Z`) as its own commit (convention:
   `Require omniagents X.Y.Z`).
2. **Run the suite** and ensure the working tree is clean:
   ```bash
   PYTHONPATH=$PWD .venv/bin/pytest tests/
   git status --porcelain=v1 --untracked-files=no
   ```
3. **Bump**: `make bump-patch` / `bump-minor` / `bump-major`
   (sanity-check first with `bump2version --dry-run --verbose patch`).
4. **Publish**: `make publish` — cleans `dist/`, runs `python -m build`
   (wheel + sdist), and uploads each artifact to
   `https://push.fury.io/$FURY_USER/`.
5. **Push**: `git push origin main --tags`.

## Quick Patch Release

```bash
PYTHONPATH=$PWD .venv/bin/pytest tests/
git status --porcelain=v1 --untracked-files=no
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
(End users: see the pipx/uv instructions in `README.md`, which keep
PyPI as the default index and add Fury as an extra index.)
