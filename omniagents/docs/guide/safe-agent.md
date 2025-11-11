# Safe Agent Mode

Safe Agent mode requires user approval before executing any tool, providing a security layer for sensitive operations.

## Enabling Safe Mode

In YAML configuration:

```yaml
name: SafeAgent
instructions_file: instructions.md
tools: [delete_file, send_email]
use_safe_agent: true
```

In Python:

```python
agent = create_agent(
    name="SafeAgent",
    instructions=instructions,
    tools=[delete_file, send_email],
    use_safe_agent=True
)
```

## How It Works

1. Agent decides to call a tool
2. Execution pauses and user is prompted
3. User approves or rejects the tool call
4. If approved, tool executes and result is returned to agent
5. If rejected, agent is informed and can try alternative approaches

## Approval Interface

The approval interface varies by backend:

### GUI Mode

Visual dialog showing:
- Tool name
- Parameters
- Description
- Approve/Reject buttons

### CLI Mode

Text prompt:
```
Agent wants to call: delete_file
Parameters:
  path: /important/file.txt

Approve? (y/n):
```

### Server Mode

JSON-RPC request to client:
```json
{
  "method": "ui.request_tool_approval",
  "params": {
    "tool_name": "delete_file",
    "parameters": {"path": "/important/file.txt"}
  }
}
```

Client responds with approval decision.

## Rich Approval Metadata

Enhance approval dialogs with additional metadata:

```python
from omniagents.core import function_tool, approval_metadata

@function_tool
@approval_metadata(
    title="Delete File",
    description="Permanently remove a file from the filesystem.",
    risk_level="high",
    category="file_operations"
)
def delete_file(path: str) -> str:
    """Delete a file from the filesystem.

    Args:
        path: Path to the file to delete
    """
    import os
    os.remove(path)
    return f"Deleted {path}"
```

The metadata enhances the approval UI with:
- **title**: Display name for the operation
- **description**: Detailed explanation
- **risk_level**: "low", "medium", or "high"
- **category**: Logical grouping

## Use Cases

Safe Agent mode is recommended for:

1. **File System Operations**: Creating, modifying, or deleting files
2. **Network Requests**: Sending emails, making API calls
3. **Database Operations**: Modifying or deleting data
4. **Financial Transactions**: Any money-related operations
5. **System Commands**: Running shell commands

## Example Safe Agent

```python
from omniagents.core import function_tool, approval_metadata

@function_tool
@approval_metadata(
    title="Send Email",
    risk_level="medium"
)
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email message."""
    # Implementation
    return f"Email sent to {to}"

@function_tool
@approval_metadata(
    title="Execute Command",
    risk_level="high"
)
def run_command(command: str) -> str:
    """Execute a shell command."""
    # Implementation
    import subprocess
    result = subprocess.run(command, shell=True, capture_output=True)
    return result.stdout.decode()
```

Agent YAML:

```yaml
name: SafeAssistant
instructions_file: instructions.md
tools: [send_email, run_command]
use_safe_agent: true
```

## Best Practices

1. **Use for Destructive Operations**: Always enable for tools that modify state
2. **Clear Descriptions**: Write detailed docstrings and approval metadata
3. **Risk Levels**: Accurately categorize risk levels
4. **User Education**: Explain what each tool does in approval prompts
5. **Audit Logging**: Consider logging all approval decisions

## Limitations

- Adds friction to the user experience
- May slow down agent execution
- Requires active user participation

## Next Steps

- [Tools](tools.md) - Learn more about creating tools
- [Session Management](sessions.md) - Understand session handling
- [Examples](../examples.md) - See complete examples
