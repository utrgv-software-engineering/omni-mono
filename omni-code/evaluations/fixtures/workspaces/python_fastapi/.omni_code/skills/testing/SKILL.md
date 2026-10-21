---
name: testing
description: Required testing configuration and conventions — tests fail with 401 without this
---

# Testing Skill

## Required Setup

Tests require the `APP_ENV=testing` environment variable. Without it, the
`AuthMiddleware` in `src/middleware/auth.py` rejects all non-public requests
with a 401 response.

Run tests with:
```
APP_ENV=testing pytest --tb=short -q
```

The shared `client` fixture in `tests/conftest.py` sets `APP_ENV=testing`
automatically. Tests that create their own `TestClient` without this
fixture will fail with 401 — the fix is to use the `client` fixture
instead of constructing a manual TestClient.

## Conventions
- Test naming: `test_<what>_<condition>_<expected>`
  - Example: `test_create_user_missing_email_returns_422`
- Use the `client` fixture from `conftest.py` for all route tests
- Group tests by resource, not by layer
- Fixtures for shared test data go in `conftest.py`
