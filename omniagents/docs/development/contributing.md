# Contributing

Thank you for your interest in contributing to OmniAgents!

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Git
- Go 1.21+ (for Bubble Tea TUI development)
- Node.js/npm (for web UI development)

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/omniagents.git
   cd omniagents
   ```

2. **Install development dependencies**:
   ```bash
   make dev
   # Or manually:
   pip install -e ".[dev]"
   ```

3. **Install optional dependencies** (as needed):
   ```bash
   pip install -e ".[all]"  # All features
   ```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest -q

# Run specific test file
pytest tests/path/to/test_file.py -q

# Run with coverage
pytest --cov=omniagents
```

### Code Formatting

Format Python code with Black before every commit:

```bash
# Format all files
black .

# Format specific file
black path/to/file.py
```

**Important**: Always run Black before committing Python changes.

### Type Checking

```bash
# Run mypy (if configured)
mypy omniagents
```

### Coverage

```bash
# All coverage (Python, Web, Go)
make coverage-all

# Python only
make coverage-python

# Web only
make coverage-web

# Go (Bubble Tea TUI) only
make coverage-go
```

## Project Structure

```
omniagents/
├── omniagents/
│   ├── core/          # Core functionality
│   ├── backends/      # Interface implementations
│   ├── rpc/           # JSON-RPC protocol
│   └── studio/        # Web UIs
├── tests/             # Test suite
├── examples/          # Example projects
└── docs/              # Documentation
```

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feature/my-feature
# or
git checkout -b fix/my-bugfix
```

### 2. Make Your Changes

- Write clear, documented code
- Add tests for new functionality
- Update documentation as needed
- Follow existing code style

### 3. Run Tests

```bash
pytest -q
```

### 4. Format Code

```bash
black .
```

### 5. Commit Changes

```bash
git add .
git commit -m "Description of changes"
```

Follow commit message conventions:
- `feat:` for new features
- `fix:` for bug fixes
- `docs:` for documentation
- `test:` for tests
- `refactor:` for refactoring

### 6. Push and Create PR

```bash
git push origin feature/my-feature
```

Then create a pull request on GitHub.

## Testing Guidelines

### Writing Tests

- Place tests in `tests/` directory
- Match directory structure of source code
- Use descriptive test names
- Include docstrings

Example:

```python
def test_function_tool_decorator():
    """Test that @function_tool properly decorates functions."""
    @function_tool
    def my_tool() -> str:
        """Test tool."""
        return "result"

    assert hasattr(my_tool, '_omni_tool')
    assert my_tool() == "result"
```

### Test Fixtures

Common fixtures are defined in `tests/conftest.py`:

```python
@pytest.fixture
def temp_db():
    """Provide a temporary database for testing."""
    with tempfile.NamedTemporaryFile() as f:
        yield f.name
```

## Documentation

### Building Docs

```bash
# Install docs dependencies
pip install -e ".[docs]"

# Serve docs locally
mkdocs serve

# Build docs
mkdocs build
```

Visit `http://127.0.0.1:8000` to view local documentation.

### Writing Docs

- Documentation is in `docs/` directory
- Use Markdown format
- Include code examples
- Link to related pages

## Multi-Language Components

### Go (Bubble Tea TUI)

Located in `omniagents/backends/bubbletea/cmd/omni-tui/`

Build:
```bash
cd omniagents/backends/bubbletea/cmd/omni-tui
go build
```

Run:
```bash
./omni-tui --help
```

### JavaScript/React (Studio)

Located in `omniagents/studio/server/web/`

Build:
```bash
cd omniagents/studio/server/web
npm install
npm run build
```

## Version Management

```bash
# Bump version
make bump-patch  # 0.1.0 -> 0.1.1
make bump-minor  # 0.1.0 -> 0.2.0
make bump-major  # 0.1.0 -> 1.0.0
```

## Code Review

Pull requests should:

1. Pass all tests
2. Include new tests for new features
3. Be formatted with Black
4. Have clear descriptions
5. Update documentation as needed

## Questions?

- Open an issue on GitHub
- Check existing issues and PRs
- Review documentation

## License

By contributing, you agree that your contributions will be licensed under the project's license.
