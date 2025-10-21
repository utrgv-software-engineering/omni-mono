import subprocess
from typing import Dict, Any, Optional
from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata


@rich_function_tool
def execute_bash(
    ctx: RunContextWrapper[Any],
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
) -> RichToolOutput:
    """
    Executes a shell command in the user's environment and returns the output.

    Args:
        command: The shell command to execute
        timeout: Maximum execution time in seconds

    Returns:
        RichToolOutput containing both text output for the LLM and rich metadata
        for UI rendering including stdout, stderr, return code

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

    # Determine working directory from explicit arg or context
    if cwd is None:
        try:
            c = getattr(ctx, "context", None)
            if isinstance(c, dict):
                wr = c.get("workspace_root")
            else:
                wr = getattr(c, "workspace_root", None)
            if isinstance(wr, str) and wr.strip():
                cwd = wr
        except Exception:
            cwd = None

    try:
        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        # Combine stdout and stderr for LLM output
        combined_output = process.stdout
        if process.stderr:
            combined_output = (
                combined_output
                + ("\n" if combined_output else "")
                + f"[stderr]\n{process.stderr}"
            )

        # Create LLM-friendly output
        if process.returncode == 0:
            if combined_output:
                llm_output = f"Command executed successfully:\n{combined_output}"
            else:
                llm_output = f"Command executed successfully with no output"
        else:
            llm_output = f"Command failed with exit code {process.returncode}:\n{combined_output}"

        # Split into lines for preview
        lines = combined_output.split("\n") if combined_output else []
        preview_limit = 10

        # Create preview (first N lines)
        if len(lines) > preview_limit:
            preview = "\n".join(lines[:preview_limit])
            truncated = True
        else:
            preview = combined_output
            truncated = False

        # Create rich metadata for UI following the standard format
        ui_metadata = {
            "value": combined_output,  # The actual command output
            "display_type": "command",
            "summary": f"$ {command}",
            "preview": preview,
            "truncated": truncated,
            "metadata": {
                "command": command,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "exit_code": process.returncode,
                "success": process.returncode == 0,
                "has_stderr": bool(process.stderr),
                "line_count": len(lines),
                "preview_lines": min(len(lines), preview_limit),
                "cwd": cwd,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except subprocess.TimeoutExpired:
        llm_output = f"Command timed out after {timeout} seconds"

        ui_metadata = {
            "value": f"Command timed out after {timeout} seconds",
            "display_type": "error",
            "summary": "Command timeout",
            "preview": f"Command '{command}' exceeded timeout of {timeout} seconds",
            "truncated": False,
            "metadata": {
                "error_type": "timeout",
                "command": command,
                "timeout": timeout,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)
    except Exception as e:
        llm_output = f"Error executing command: {str(e)}"

        ui_metadata = {
            "value": f"Error executing command: {str(e)}",
            "display_type": "error",
            "summary": "Execution error",
            "preview": f"Failed to execute '{command}': {str(e)}",
            "truncated": False,
            "metadata": {
                "error_type": "execution_error",
                "command": command,
                "error": str(e),
            },
        }

        return RichToolOutput(llm_output, ui_metadata)


# --- Approval metadata for execute_bash: render the command nicely in approval dialog ---
@approval_metadata()
def execute_bash_approval(command: str, timeout: Optional[int] = None) -> dict:
    return {
        "display_type": "command",
        "summary": "Run shell command",
        "value": command,
        "preview": f"$ {command}",
        "truncated": False,
        "metadata": {
            "command": command,
            "timeout": timeout,
        },
    }
