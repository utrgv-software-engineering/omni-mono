import pytest
from unittest.mock import patch, MagicMock
import subprocess

# Mock the decorator before importing the module
@pytest.fixture(autouse=True)
def mock_function_tool():
    """Mock the function_tool decorator to simply return the original function."""
    with patch('omniagents.core.function_tool', side_effect=lambda f: f):
        yield


@pytest.fixture(autouse=True)
def mock_git_validation():
    with patch('examples.coding_agent.tools.bash_tool._validate_git_environment', return_value={"valid": True}):
        yield


def test_execute_bash_success(mock_function_tool):
    """Test successful execution of a shell command."""
    # Prepare the mocked CompletedProcess-like object
    mock_completed = MagicMock()
    mock_completed.stdout = "hello\n"
    mock_completed.stderr = ""
    mock_completed.returncode = 0

    with patch('examples.coding_agent.tools.bash_tool.subprocess.run', return_value=mock_completed) as mock_run:
        from examples.coding_agent.tools.bash_tool import execute_bash

        # Call the tool
        result = execute_bash(command='echo "hello"', timeout=10)

        # Assertions
        assert result["stdout"].strip() == "hello"
        assert result["stderr"] == ""
        assert result["returncode"] == 0
        mock_run.assert_called_once_with(
            'echo "hello"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10
        )


def test_execute_bash_timeout(mock_function_tool):
    """Test timeout handling when command exceeds specified timeout."""
    with patch('examples.coding_agent.tools.bash_tool.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='sleep 5', timeout=2)):
        from examples.coding_agent.tools.bash_tool import execute_bash

        result = execute_bash(command='sleep 5', timeout=2)

        assert result["error"].startswith("Command timed out")
        assert "2 seconds" in result["error"]


def test_execute_bash_generic_error(mock_function_tool):
    """Test generic exception handling during command execution."""
    with patch('examples.coding_agent.tools.bash_tool.subprocess.run', side_effect=Exception("Boom")):
        from examples.coding_agent.tools.bash_tool import execute_bash

        result = execute_bash(command='invalid_command', timeout=5)

        assert result["error"].startswith("Error executing command")
        assert "Boom" in result["error"] 
