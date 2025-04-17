import subprocess
from typing import Dict, Any, Optional
from agents import function_tool

@function_tool
def execute_bash(command: str, timeout: Optional[int]) -> Dict[str, Any]:
    """
    Executes a shell command in the user's environment and returns the output.
    
    Args:
        command: The shell command to execute
        timeout: Maximum execution time in seconds
        
    Returns:
        Dictionary containing:
        {
            "stdout": Command standard output (if successful)
            "stderr": Command standard error (if any)
            "returncode": Command exit code (0 indicates success)
            "error": Error message (if command execution failed)
        }
        
    Usage:
        Use this tool to execute shell commands for file operations, system
        queries, or any other CLI operations within the operating system.
        
    Safety Notes:
        - This tool executes commands directly in the system shell
        - Be careful with commands that can modify system state
        - Avoid running untrusted code or potentially harmful commands
        - If timeout is not provided, a default value of 30 seconds will be used
        
    Example:
        # List files in the current directory
        execute_bash("ls -la", timeout=30)
        
        # Find a file
        execute_bash("find . -name '*.py' -type f", timeout=60)
    """
    # Default timeout value if not provided
    if timeout is None:
        timeout = 30
        
    result = {
        "stdout": "",
        "stderr": "",
        "returncode": None
    }
    
    try:
        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        result["stdout"] = process.stdout
        result["stderr"] = process.stderr
        result["returncode"] = process.returncode
        
        return result
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout} seconds"}
    except Exception as e:
        return {"error": f"Error executing command: {str(e)}"}
