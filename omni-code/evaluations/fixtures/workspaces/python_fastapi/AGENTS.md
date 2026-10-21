# Project Conventions

## Python Style
- Use **snake_case** for all function and variable names
- All functions must include **type hints** for parameters and return values
- All public functions must have **Google-style docstrings**
- Maximum line length: 100 characters

## Import Ordering
1. Standard library
2. Third-party packages
3. Local imports

Separate each group with a blank line.

## Architecture
- Routes handle HTTP concerns only — delegate logic to services
- Services contain business logic and call the database layer
- Schemas define request/response shapes (Pydantic models)
- Models define domain objects (dataclasses)
- All routes require API key authentication via the auth middleware

## Error Handling
- Services raise `ValueError` for validation errors
- Routes catch `ValueError` and return 422 responses
- Routes catch `KeyError` and return 404 responses

## Testing
- Use **pytest** for all tests (not unittest)
- Tests go in `tests/` mirroring the `src/` structure
- Prefix test files with `test_`
- Use the shared `client` fixture from `conftest.py` for route tests
- Run tests with: `APP_ENV=testing pytest --tb=short -q`

## Git
- All PRs must include a JIRA ticket ID in the title (e.g., `PROJ-123: description`)
- Do not force-push to main
