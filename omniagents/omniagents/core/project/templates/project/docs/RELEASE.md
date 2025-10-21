# Release Process

## Prerequisites

- Install build tooling: `pip install build twine bump2version`
- Configure PyPI credentials: `export TWINE_USERNAME=__token__` and set `TWINE_PASSWORD`
- Configure Gemfury credentials in `.env` or your shell:
  ```
  FURY_USER=your_username
  FURY_TOKEN=your_token
  ```
- Ensure the working tree is clean: `git status --porcelain=v1 --untracked-files=no`

## Version Management

1. Choose the bump type and run one of `make bump-patch`, `make bump-minor`, or `make bump-major`. This updates `pyproject.toml`, commits, and tags the release automatically.
2. Push the commit and tag: `git push && git push --tags`.

## Local Publish to PyPI

```bash
make clean
make publish-pypi
```

## Local Publish to Gemfury

```bash
make clean
make publish-gemfury
```

## Installing from Gemfury

```bash
make install-gemfury
```

## Continuous Delivery

Workflows `publish-pypi.yml` and `publish-gemfury.yml` trigger automatically when you push a `v*` tag. Set repository secrets `PYPI_API_TOKEN`, `TEST_PYPI_API_TOKEN`, `FURY_USER`, and `FURY_TOKEN` before running them. After the uploads complete, a GitHub release draft is generated for that tag so you can finalize the notes.
