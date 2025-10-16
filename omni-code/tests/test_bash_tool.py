from unittest.mock import patch, MagicMock
import subprocess
from tools.bash_tool import execute_bash


def test_execute_bash_success(invoke_tool):
    """Test successful execution of a shell command."""
    # Prepare the mocked CompletedProcess-like object
    mock_completed = MagicMock()
    mock_completed.stdout = "hello\n"
    mock_completed.stderr = ""
    mock_completed.returncode = 0

    with patch('tools.bash_tool.subprocess.run', return_value=mock_completed) as mock_run:
        result = invoke_tool(execute_bash, workspace_root='.', command='echo "hello"', timeout=10)
        meta = result.ui_metadata['metadata']

        assert meta["stdout"].strip() == "hello"
        assert meta["stderr"] == ""
        assert meta["exit_code"] == 0
        mock_run.assert_called_once_with(
            'echo "hello"',
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
            cwd='.'
        )


def test_execute_bash_timeout(invoke_tool):
    """Test timeout handling when command exceeds specified timeout."""
    with patch('tools.bash_tool.subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='sleep 5', timeout=2)):
        result = invoke_tool(execute_bash, workspace_root='.', command='sleep 5', timeout=2)
        assert "Command timed out" in result.ui_metadata["value"]
        assert result.ui_metadata["metadata"]["error_type"] == "timeout"


def test_execute_bash_generic_error(invoke_tool):
    """Test generic exception handling during command execution."""
    with patch('tools.bash_tool.subprocess.run', side_effect=Exception("Boom")):
        result = invoke_tool(execute_bash, workspace_root='.', command='invalid_command', timeout=5)
        assert "Error executing command" in result.ui_metadata["value"]
        assert "Boom" in result.ui_metadata["value"]
        assert result.ui_metadata["metadata"]["error_type"] == "execution_error"
