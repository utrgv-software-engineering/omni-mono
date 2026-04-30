import os
import shutil
import subprocess
import time
from typing import Dict, Any, Optional
from agents.run_context import RunContextWrapper
from omniagents.core.tools import rich_function_tool, RichToolOutput, approval_metadata


# Constants for execute_bash
_BASH_DEFAULT_TIMEOUT = 30
_BASH_DEFAULT_MAX_OUTPUT_CHARS = 40000  # ~40KB, reasonable for most commands
_BASH_MAX_LINE_LENGTH = 500


def _command_summary(exit_code: int, wall_time_ms: int, truncated: bool) -> str:
    """Build a concise summary string for the ToolCard header."""
    parts = [f"exit {exit_code}", f"{wall_time_ms}ms"]
    if truncated:
        parts.append("truncated")
    return f"Ran command ({', '.join(parts)})"


def _truncate_output_head_tail(
    text: str, max_chars: int
) -> tuple[str, int, bool]:
    """
    Truncate text preserving head and tail, dropping the middle.

    Returns (truncated_text, chars_removed, was_truncated).
    """
    if len(text) <= max_chars:
        return text, 0, False

    if max_chars <= 0:
        return "", len(text), True

    # Split budget 50/50 between head and tail
    head_budget = max_chars // 2
    tail_budget = max_chars - head_budget

    # Find safe truncation points at character boundaries
    head = text[:head_budget]
    tail = text[-tail_budget:] if tail_budget > 0 else ""

    chars_removed = len(text) - len(head) - len(tail)

    truncated = f"{head}\n\n[...{chars_removed:,} characters truncated...]\n\n{tail}"

    return truncated, chars_removed, True


def _truncate_long_lines(text: str, max_line_length: int = _BASH_MAX_LINE_LENGTH) -> tuple[str, int]:
    """
    Truncate individual lines that exceed max_line_length.

    Returns (processed_text, count_of_truncated_lines).
    """
    lines = text.split("\n")
    result_lines = []
    truncated_count = 0

    for line in lines:
        if len(line) > max_line_length:
            result_lines.append(line[:max_line_length] + "...")
            truncated_count += 1
        else:
            result_lines.append(line)

    return "\n".join(result_lines), truncated_count


@rich_function_tool(client_status="Running shell command...")
def execute_bash(
    ctx: RunContextWrapper[Any],
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    max_output_chars: Optional[int] = None,
) -> RichToolOutput:
    """
    Executes a shell command in the user's environment and returns the output.

    Args:
        command: The shell command to execute
        timeout: Maximum execution time in seconds (default: 30)
        cwd: Working directory for the command
        max_output_chars: Maximum characters to return (default: 40000).
            If output exceeds this, the middle is truncated while preserving
            the beginning and end.

    Returns:
        RichToolOutput containing both text output for the LLM and rich metadata
        for UI rendering including stdout, stderr, return code, and timing.

    Usage:
        Use this tool to execute shell commands for file operations, system
        queries, or any other CLI operations within the operating system.

    Output format:
        - Shows exit code and wall time
        - Lines longer than 500 characters are truncated
        - If total output exceeds max_output_chars, middle content is dropped
          with a notice like "[...N characters truncated...]"
        - Truncation notices inform you of omitted content

    Safety Notes:
        - This tool executes commands directly in the system shell
        - Be careful with commands that can modify system state
        - Avoid running untrusted code or potentially harmful commands

    Examples:
        # List files in the current directory
        execute_bash("ls -la")

        # Run a command with custom timeout
        execute_bash("npm install", timeout=120)

        # Run a command that may produce lots of output
        execute_bash("find / -name '*.log'", max_output_chars=10000)
    """
    # Set defaults
    if timeout is None:
        timeout = _BASH_DEFAULT_TIMEOUT
    if max_output_chars is None:
        max_output_chars = _BASH_DEFAULT_MAX_OUTPUT_CHARS

    # Determine working directory. Mirrors file_tools._resolve_path:
    # relative cwd is anchored to workspace_root when one is available,
    # so bash and file tools agree on what "." means.
    wr: Optional[str] = None
    try:
        c = getattr(ctx, "context", None)
        if isinstance(c, dict):
            raw_wr = c.get("workspace_root")
        else:
            raw_wr = getattr(c, "workspace_root", None)
        if isinstance(raw_wr, str) and raw_wr.strip():
            wr = os.path.abspath(raw_wr)
    except Exception:
        wr = None

    if cwd is None or (isinstance(cwd, str) and not cwd.strip()):
        cwd = wr
    elif not os.path.isabs(cwd) and wr:
        cwd = os.path.abspath(os.path.join(wr, cwd))

    start_time = time.monotonic()

    try:
        run_kwargs: Dict[str, Any] = {}
        if os.name != "nt":
            shell_executable = None
            for candidate in ("/bin/bash", "/usr/bin/bash"):
                if os.path.exists(candidate):
                    shell_executable = candidate
                    break
            if shell_executable is None:
                shell_executable = shutil.which("bash")
            if shell_executable:
                run_kwargs["executable"] = shell_executable

        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            **run_kwargs,
        )

        wall_time_ms = int((time.monotonic() - start_time) * 1000)

        # Combine stdout and stderr
        combined_output = process.stdout
        if process.stderr:
            combined_output = (
                combined_output
                + ("\n" if combined_output else "")
                + f"[stderr]\n{process.stderr}"
            )

        original_length = len(combined_output)
        total_lines = combined_output.count("\n") + 1 if combined_output else 0

        # Truncate long individual lines first
        combined_output, lines_truncated = _truncate_long_lines(
            combined_output, _BASH_MAX_LINE_LENGTH
        )

        # Then truncate overall output if needed (head/tail preservation)
        combined_output, chars_removed, was_truncated = _truncate_output_head_tail(
            combined_output, max_output_chars
        )

        # Build LLM output with metadata header
        header_lines = []

        # Status line
        if process.returncode == 0:
            header_lines.append(f"Exit code: 0 (success) | Wall time: {wall_time_ms}ms")
        else:
            header_lines.append(f"Exit code: {process.returncode} (failed) | Wall time: {wall_time_ms}ms")

        # Truncation notices
        if was_truncated or lines_truncated > 0:
            header_lines.append(f"Total output: {original_length:,} chars, {total_lines:,} lines")
            if lines_truncated > 0:
                header_lines.append(f"[{lines_truncated} lines exceeded {_BASH_MAX_LINE_LENGTH} chars and were truncated]")
            if was_truncated:
                header_lines.append(f"[Output truncated: {chars_removed:,} characters removed from middle]")

        if header_lines:
            header = "\n".join(header_lines) + "\n\n"
        else:
            header = ""

        if combined_output:
            llm_output = header + combined_output
        else:
            llm_output = header + "(no output)"

        # Create preview for UI (first 20 lines)
        preview_lines = combined_output.split("\n")[:20] if combined_output else []
        preview = "\n".join(preview_lines)
        ui_truncated = len(combined_output.split("\n")) > 20 if combined_output else False

        # Create rich metadata for UI
        ui_metadata = {
            "value": combined_output,
            "display_type": "command",
            "summary": _command_summary(process.returncode, wall_time_ms, was_truncated),
            "preview": preview,
            "truncated": ui_truncated or was_truncated,
            "metadata": {
                "command": command,
                "stdout": process.stdout,
                "stderr": process.stderr,
                "exit_code": process.returncode,
                "success": process.returncode == 0,
                "has_stderr": bool(process.stderr),
                "wall_time_ms": wall_time_ms,
                "original_chars": original_length,
                "returned_chars": len(combined_output),
                "chars_truncated": chars_removed,
                "lines_truncated": lines_truncated,
                "total_lines": total_lines,
                "was_truncated": was_truncated,
                "cwd": cwd,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except subprocess.TimeoutExpired:
        wall_time_ms = int((time.monotonic() - start_time) * 1000)
        llm_output = f"Command timed out after {timeout} seconds (wall time: {wall_time_ms}ms)"

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
                "wall_time_ms": wall_time_ms,
            },
        }

        return RichToolOutput(llm_output, ui_metadata)

    except Exception as e:
        wall_time_ms = int((time.monotonic() - start_time) * 1000)
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
                "wall_time_ms": wall_time_ms,
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
