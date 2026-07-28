# Release Process

This document describes how to create a new release for OmniAgents.

## Prerequisites

1. Install development dependencies:
   ```bash
   pip install -e ".[dev]"
   ```

2. Ensure you have PyPI API tokens set up:
   - `PYPI_API_TOKEN` - For production PyPI
   - `TEST_PYPI_API_TOKEN` - For test PyPI

3. Add these tokens as GitHub secrets in your repository settings.

## Release Steps

### 1. Update CHANGELOG

Edit `CHANGELOG.md` and move items from "Unreleased" to the new version section.

### 2. Bump Version

Choose the appropriate version bump:

```bash
# For bug fixes (0.1.3 → 0.1.4)
bump2version patch

# For new features (0.1.3 → 0.2.0)
bump2version minor

# For breaking changes (0.1.3 → 1.0.0)
bump2version major
```

This will:
- Update version in `omniagents/_version.py`
- Update version in CHANGELOG.md
- Create a git commit
- Create a git tag (e.g., `v0.1.4`)

### 3. Push Changes

```bash
git push origin main
git push origin --tags
```

### 4. Create GitHub Release

1. Go to GitHub releases page
2. Click "Draft a new release"
3. Select the tag you just created
4. Use the tag name as the release title (e.g., "v0.1.4")
5. Copy the relevant section from CHANGELOG.md as the release description
6. Click "Publish release"

This will automatically trigger the PyPI publishing workflow.

## Pre-release Testing

For testing releases:

```bash
# Create a pre-release version
bump2version patch --tag-name v0.1.4rc1 --new-version 0.1.4rc1

# Manually trigger the workflow to publish to Test PyPI
# Go to Actions → Publish to PyPI → Run workflow
```

## Version Numbering Guidelines

- **PATCH**: Bug fixes, documentation updates, minor improvements
- **MINOR**: New features, significant improvements (backwards compatible)
- **MAJOR**: Breaking API changes, major architectural changes

## Rollback Process

If something goes wrong:

```bash
# Delete the tag locally
git tag -d v0.1.4

# Delete the tag remotely
git push --delete origin v0.1.4

# Reset to previous commit
git reset --hard HEAD~1

# Force push (be careful!)
git push --force origin main
```

## Checklist

Before releasing:
- [ ] All tests pass
- [ ] Documentation is updated
- [ ] CHANGELOG.md is updated
- [ ] Version number follows SemVer
- [ ] Breaking changes are clearly documented
- [ ] If the GUI protocol changed, generation and v1 compatibility checks pass as documented in [GUI Protocol Governance](../docs/development/gui-protocol-governance.md)
- [ ] If the GUI protocol changed, Omni Desktop sync provenance identifies the reviewed OmniAgents commit and the active Desktop reviewer approved the release-train impact
