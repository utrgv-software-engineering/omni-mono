# Testing

OmniAgents uses pytest for testing with comprehensive coverage across Python, Go, and JavaScript components.

## Running Tests

### Quick Test Run

```bash
pytest -q
```

### Specific Test File

```bash
pytest tests/path/to/test_file.py -q
```

### With Coverage

```bash
pytest --cov=omniagents
```

### Verbose Output

```bash
pytest -v
```

## Test Organization

Tests are organized in the `tests/` directory, mirroring the source structure:

```
tests/
├── conftest.py          # Shared fixtures
├── core/
│   ├── test_agents.py
│   ├── test_tools.py
│   └── test_config.py
├── backends/
│   ├── test_cli.py
│   ├── test_server.py
│   └── test_flet.py
└── rpc/
    └── test_protocol.py
```

## Test Fixtures

Common fixtures are defined in `tests/conftest.py`:

### Temporary Database

```python
@pytest.fixture
def temp_db():
    """Provide a temporary SQLite database."""
    with tempfile.NamedTemporaryFile(suffix='.db') as f:
        yield f.name
```

### Mock Agent

```python
@pytest.fixture
def mock_agent():
    """Provide a mock agent for testing."""
    agent = create_agent(
        name="TestAgent",
        instructions="Test instructions",
        tools=[]
    )
    return agent
```

## Writing Tests

### Basic Test Structure

```python
def test_function_tool_decorator():
    """Test that @function_tool properly decorates functions."""
    @function_tool
    def my_tool() -> str:
        """Test tool."""
        return "result"

    # Assertions
    assert hasattr(my_tool, '_omni_tool')
    assert my_tool() == "result"
```

### Async Tests

```python
@pytest.mark.asyncio
async def test_remote_agent_connection():
    """Test RemoteAgent WebSocket connection."""
    agent = RemoteAgent("ws://localhost:8000/ws")
    await agent.connect()
    assert agent.connected
    await agent.close()
```

### Parametrized Tests

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
])
def test_uppercase(input, expected):
    """Test uppercase conversion."""
    assert input.upper() == expected
```

## Coverage

### All Components

```bash
make coverage-all
```

This runs:
- Python coverage (pytest)
- Go coverage (Bubble Tea TUI)
- Web coverage (Vitest)

### Python Coverage

```bash
make coverage-python
# Or:
pytest --cov=omniagents --cov-report=html
```

View HTML report:
```bash
open htmlcov/index.html
```

### Go Coverage (Bubble Tea TUI)

```bash
make coverage-go
```

Coverage for the Go-based TUI backend.

### Web Coverage (React/Vitest)

```bash
make coverage-web
```

Coverage for Studio web UIs.

## Test Categories

### Unit Tests

Test individual functions and classes in isolation:

```python
def test_tool_discovery():
    """Test tool discovery from directory."""
    tools = discover_functions_in_dir("examples/basic_python/tools")
    assert len(tools) > 0
```

### Integration Tests

Test interactions between components:

```python
@pytest.mark.asyncio
async def test_agent_with_tools():
    """Test agent execution with tools."""
    @function_tool
    def test_tool() -> str:
        return "result"

    agent = create_agent(
        name="TestAgent",
        instructions="Use the test_tool",
        tools=[test_tool]
    )

    result = await agent.run("Call test_tool")
    assert "result" in result
```

### End-to-End Tests

Test complete workflows:

```python
@pytest.mark.asyncio
async def test_remote_agent_workflow():
    """Test complete remote agent workflow."""
    # Start server
    # Connect client
    # Send messages
    # Verify responses
    pass
```

## Mocking

### Mock External Services

```python
from unittest.mock import Mock, patch

@patch('requests.get')
def test_api_tool(mock_get):
    """Test tool that calls external API."""
    mock_get.return_value.json.return_value = {"data": "test"}

    @function_tool
    def fetch_data(url: str) -> dict:
        return requests.get(url).json()

    result = fetch_data("https://api.example.com")
    assert result == {"data": "test"}
```

### Mock OpenAI API

```python
@patch('openai.ChatCompletion.create')
def test_agent_response(mock_create):
    """Test agent with mocked OpenAI API."""
    mock_create.return_value = {
        "choices": [{"message": {"content": "Test response"}}]
    }

    # Test agent execution
    pass
```

## Test Configuration

Configuration in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
addopts = "-ra"
testpaths = ["tests"]

[tool.coverage.run]
branch = true
source = ["omniagents"]
omit = [
    "omniagents/backends/bubbletea/cmd/omni-tui/*",
    "omniagents/backends/ink/tui/*",
]
```

## Continuous Integration

Tests run automatically on:
- Pull requests
- Commits to main branch

GitHub Actions workflow: `.github/workflows/test.yml`

## Best Practices

1. **Test Names**: Use descriptive names that explain what is being tested
2. **Docstrings**: Add docstrings to tests for clarity
3. **Arrange-Act-Assert**: Structure tests clearly
4. **Fixtures**: Use fixtures for common setup
5. **Mocking**: Mock external dependencies
6. **Coverage**: Aim for high coverage but focus on meaningful tests
7. **Fast Tests**: Keep tests fast by mocking slow operations

## Debugging Tests

### Run Single Test

```bash
pytest tests/test_file.py::test_function_name -v
```

### Print Output

```bash
pytest -s  # Show print statements
```

### Debugging with pdb

```python
def test_something():
    import pdb; pdb.set_trace()
    # Debug here
    assert True
```

### Pytest Debugging

```bash
pytest --pdb  # Drop into debugger on failure
```

## Test Data

Test data and fixtures:
- Use `tests/fixtures/` for test data files
- Use `tempfile` for temporary files
- Clean up after tests

Example:

```python
import tempfile
import os

def test_with_temp_file():
    """Test with temporary file."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test data")
        temp_path = f.name

    try:
        # Run test with temp_path
        pass
    finally:
        os.unlink(temp_path)
```

## Next Steps

- [Contributing](contributing.md) - Development guidelines
- [Examples](../examples.md) - Example projects
