# Safe Agent Mode

Safe Agent mode requires user approval before executing tools, providing a security layer for sensitive operations. This prevents agents from performing destructive or sensitive actions without explicit user consent.

## Enabling Safe Mode

### Basic Configuration

Enable safe mode in your `agent.yml`:

```yaml
name: safe_assistant
instructions_file: instructions.md
tools: [delete_file, send_email, execute_command]
use_safe_agent: true
```

When enabled, the agent will request approval before executing **any** tool.

### Advanced Configuration

Fine-tune safe mode behavior with `safe_agent_options`:

```yaml
name: safe_assistant
instructions_file: instructions.md
tools: [read_file, write_file, delete_file, search_web]
use_safe_agent: true

safe_agent_options:
  # Skip all approvals (effectively disables safe mode)
  skip_approvals: false

  # Stop execution if user rejects a tool
  halt_on_rejection: true

  # Tools that don't require approval (exact names)
  safe_tool_names:
    - read_file
    - search_web

  # Tools matching patterns don't require approval (regex)
  safe_tool_patterns:
    - "^get_.*"      # All tools starting with "get_"
    - ".*_read$"     # All tools ending with "_read"
```

## Configuration Options

### safe_agent_options

#### skip_approvals
**Type**: `bool` (default: `false`)

When `true`, bypasses all approval checks. Useful for testing or when you temporarily want to disable approvals without changing tool configuration.

```yaml
safe_agent_options:
  skip_approvals: true  # All tools execute without approval
```

#### halt_on_rejection
**Type**: `bool` (default: `true`)

Controls what happens when a user rejects a tool:
- `true`: Agent run stops immediately
- `false`: Agent continues and can try alternative approaches

```yaml
safe_agent_options:
  halt_on_rejection: false  # Agent continues after rejection
```

#### safe_tool_names
**Type**: `list[str]` (default: `[]`)

List of exact tool names that don't require approval. Case-sensitive.

```yaml
safe_agent_options:
  safe_tool_names:
    - read_file
    - get_current_time
    - search_database
```

#### safe_tool_patterns
**Type**: `list[str]` (default: `[]`)

List of regex patterns. Tools matching any pattern don't require approval.

```yaml
safe_agent_options:
  safe_tool_patterns:
    - "^get_.*"        # Matches: get_weather, get_user, etc.
    - ".*_readonly$"   # Matches: db_readonly, api_readonly, etc.
    - "list_.*"        # Matches: list_files, list_users, etc.
```

## How It Works

When safe mode is enabled, the following flow occurs:

1. **Agent Decision**: Agent decides to call a tool based on the conversation
2. **Safety Check**: Framework checks if tool requires approval:
   - If tool is in `safe_tool_names` → Skip to step 5
   - If tool matches `safe_tool_patterns` → Skip to step 5
   - Otherwise → Continue to step 3
3. **Request Approval**: Execution pauses and approval request is sent to user
4. **User Decision**: User reviews tool call and either approves or rejects
5. **Execute or Halt**:
   - If approved (or safe tool): Tool executes, result returned to agent
   - If rejected and `halt_on_rejection=true`: Run stops
   - If rejected and `halt_on_rejection=false`: Agent informed of rejection, can try alternatives

## Approval Interface by Backend

The approval interface adapts to each backend mode:

### GUI Mode (Flet)

Visual dialog displaying:
- Tool name and description
- All parameters and their values
- Approval metadata (if provided)
- **Approve** / **Reject** buttons
- Option to always approve this tool (adds to safe list)

### TUI Mode (Bubble Tea)

Interactive terminal prompt:
```
┌─────────────────────────────────────────────────────┐
│ Tool Approval Required                              │
├─────────────────────────────────────────────────────┤
│ Tool: delete_file                                   │
│                                                     │
│ Parameters:                                         │
│   path: /important/document.txt                     │
│   force: true                                       │
│                                                     │
│ Description:                                        │
│   Delete a file from the filesystem                 │
│                                                     │
│ [A]pprove  [R]eject  [S]kip Future                 │
└─────────────────────────────────────────────────────┘
```

### Server Mode (RPC)

Server sends `ui.request_tool_approval` client function call:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "ui.request_tool_approval",
  "params": {
    "tool_name": "delete_file",
    "args": {
      "path": "/important/document.txt",
      "force": true
    },
    "metadata": {
      "display_type": "warning",
      "summary": "Delete file",
      "value": "Permanently delete /important/document.txt",
      "metadata": {
        "risk_level": "high"
      }
    }
  }
}
```

Client must respond with:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "approved": true,
    "always_approve": false
  }
}
```

**Response Fields:**
- `approved` (bool): Whether to execute the tool
- `always_approve` (bool): If true, adds tool to safe list for remainder of session

## Rich Approval Metadata

Enhance approval dialogs with custom metadata using the `@approval_metadata` decorator.

### Creating Approval Metadata

Define a function that generates approval metadata. The function must:
1. Have the same signature as the tool
2. Be decorated with `@approval_metadata()`
3. Return a dictionary with display information

**Example:**

```python
from omniagents import function_tool
from omniagents.core import approval_metadata

# Define the tool
@function_tool
def delete_file(path: str, force: bool = False) -> str:
    """Delete a file from the filesystem.

    Args:
        path: Path to the file to delete
        force: Skip confirmation if true
    """
    import os
    os.remove(path)
    return f"Deleted {path}"

# Define approval metadata generator
@approval_metadata()  # Auto-detects tool name from function name
def delete_file_approval(path: str, force: bool = False) -> dict:
    """Generate approval metadata for file deletion."""
    import os
    file_exists = os.path.exists(path)
    file_size = os.path.getsize(path) if file_exists else 0

    return {
        "display_type": "warning",
        "summary": f"Delete {os.path.basename(path)}",
        "value": f"Permanently delete: {path}",
        "preview": f"File: {path}\nSize: {file_size} bytes",
        "truncated": False,
        "metadata": {
            "risk_level": "high" if force else "medium",
            "file_path": path,
            "file_exists": file_exists,
            "file_size": file_size
        }
    }
```

### Metadata Format

The returned dictionary should follow this structure:

```python
{
    "display_type": str,    # UI rendering hint (warning, command, diff, etc.)
    "summary": str,         # One-line description
    "value": Any,           # Main content to display
    "preview": str,         # Preview text (optional)
    "truncated": bool,      # Whether content was truncated
    "metadata": dict        # Additional structured data
}
```

**Common Display Types:**
- `"warning"` - Dangerous operations (delete, modify)
- `"command"` - Shell commands
- `"diff"` - File changes
- `"info"` - Informational operations

### Naming Conventions

The `@approval_metadata()` decorator auto-detects the tool name:

```python
# These patterns work:
@approval_metadata()
def tool_name_approval(...):  # For tool: "tool_name"
    ...

@approval_metadata()
def tool_name_metadata(...):  # For tool: "tool_name"
    ...

# Or specify explicitly:
@approval_metadata(tool="my_tool")
def generate_approval_info(...):  # For tool: "my_tool"
    ...
```

## Use Cases

Safe Agent mode is essential for:

### 1. File System Operations
Any tools that create, modify, or delete files:

```yaml
tools:
  - write_file
  - delete_file
  - move_file

use_safe_agent: true
safe_agent_options:
  safe_tool_names:
    - read_file  # Read-only operations are safe
    - list_directory
```

### 2. Network & External Services
Tools that interact with external systems:

```yaml
tools:
  - send_email
  - post_to_api
  - upload_file
  - make_payment

use_safe_agent: true
```

### 3. Database Operations
Any tools that modify data:

```yaml
tools:
  - update_record
  - delete_record
  - execute_sql

use_safe_agent: true
safe_agent_options:
  safe_tool_names:
    - query_database  # Read-only queries are safe
```

### 4. System Commands
Shell command execution:

```yaml
tools:
  - execute_bash
  - run_script

use_safe_agent: true
```

### 5. Sensitive Data Access
Tools that access confidential information:

```yaml
tools:
  - get_api_keys
  - read_credentials
  - access_patient_records

use_safe_agent: true
```

## Complete Example

**Project Structure:**
```
safe_assistant/
├── project.yml
└── agents/
    └── file_manager/
        ├── agent.yml
        ├── instructions.md
        └── tools/
            └── file_tools.py
```

**agent.yml:**
```yaml
name: file_manager
label: File Manager Assistant
description: "Manages files with user approval for destructive operations"

model: gpt-4o-mini
instructions_file: instructions.md

tools:
  - read_file
  - write_file
  - delete_file
  - list_directory

use_safe_agent: true
safe_agent_options:
  halt_on_rejection: true
  safe_tool_names:
    - read_file
    - list_directory
  safe_tool_patterns:
    - "^list_.*"
```

**tools/file_tools.py:**
```python
from omniagents import function_tool
from omniagents.core import approval_metadata
import os

# Safe tool - no approval needed (in safe_tool_names)
@function_tool
def read_file(path: str) -> str:
    """Read and return file contents."""
    with open(path, 'r') as f:
        return f.read()

# Requires approval
@function_tool
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    with open(path, 'w') as f:
        f.write(content)
    return f"Wrote {len(content)} bytes to {path}"

@approval_metadata()
def write_file_approval(path: str, content: str) -> dict:
    """Generate approval metadata for file write."""
    file_exists = os.path.exists(path)
    content_preview = content[:100] + "..." if len(content) > 100 else content

    return {
        "display_type": "diff" if file_exists else "info",
        "summary": f"Write to {os.path.basename(path)}",
        "value": content_preview,
        "preview": f"{'Overwrite' if file_exists else 'Create'}: {path}",
        "truncated": len(content) > 100,
        "metadata": {
            "operation": "overwrite" if file_exists else "create",
            "path": path,
            "size": len(content),
            "risk_level": "medium"
        }
    }

# Requires approval - high risk
@function_tool
def delete_file(path: str) -> str:
    """Permanently delete a file."""
    os.remove(path)
    return f"Deleted {path}"

@approval_metadata()
def delete_file_approval(path: str) -> dict:
    """Generate approval metadata for file deletion."""
    file_exists = os.path.exists(path)
    file_size = os.path.getsize(path) if file_exists else 0

    return {
        "display_type": "warning",
        "summary": f"⚠️ Delete {os.path.basename(path)}",
        "value": f"Permanently delete: {path}\nSize: {file_size} bytes",
        "preview": f"DELETE: {path}",
        "truncated": False,
        "metadata": {
            "operation": "delete",
            "path": path,
            "file_exists": file_exists,
            "file_size": file_size,
            "risk_level": "high"
        }
    }
```

## Best Practices

### 1. Classify Tools by Risk

Organize tools into risk categories:

```yaml
safe_agent_options:
  # No approval needed
  safe_tool_names:
    - read_file
    - list_files
    - search
    - get_info

  # Approval required (not in safe lists)
  # - write_file (medium risk)
  # - delete_file (high risk)
  # - execute_command (critical risk)
```

### 2. Use Descriptive Approval Metadata

Always provide context in approval metadata:

```python
@approval_metadata()
def dangerous_operation_approval(target: str) -> dict:
    return {
        "display_type": "warning",
        "summary": "⚠️ High-Risk Operation",
        "value": f"This will PERMANENTLY affect {target}",
        "metadata": {
            "risk_level": "critical",
            "reversible": False,
            "affected_resources": [target]
        }
    }
```

### 3. Configure halt_on_rejection Appropriately

```yaml
# For critical operations - stop if rejected
safe_agent_options:
  halt_on_rejection: true

# For exploratory agents - continue with alternatives
safe_agent_options:
  halt_on_rejection: false
```

### 4. Use Patterns for Consistency

```yaml
safe_agent_options:
  safe_tool_patterns:
    - "^get_.*"      # All getters are safe
    - "^list_.*"     # All listers are safe
    - "^search_.*"   # All search operations are safe
    - ".*_readonly$" # Tools explicitly marked readonly
```

### 5. Document Tool Safety

In tool docstrings, clearly indicate side effects:

```python
@function_tool
def delete_user_data(user_id: str) -> str:
    """Delete all data for a user.

    **WARNING**: This operation is IRREVERSIBLE.
    - Deletes user profile
    - Removes all user content
    - Deletes associated files
    - Cannot be undone

    Args:
        user_id: ID of user to delete
    """
    # Implementation...
```

### 6. Test with skip_approvals First

During development, test agents without approval friction:

```yaml
# Development
safe_agent_options:
  skip_approvals: true  # Test without interruptions

# Production
safe_agent_options:
  skip_approvals: false  # Enable approvals
```

## Limitations & Considerations

### User Experience
- **Adds friction**: Each approval interrupts agent flow
- **Requires attention**: User must actively monitor and respond
- **May slow iteration**: Repeated approvals can be tedious

**Mitigation**: Use `safe_tool_names` and `safe_tool_patterns` to reduce unnecessary approvals

### False Sense of Security
- Approval doesn't validate tool logic
- Users may approve without fully understanding
- Tools can still have bugs or unintended effects

**Mitigation**: Write clear approval metadata and comprehensive tool docstrings

### Not a Substitute for Testing
- Safe mode doesn't replace unit tests
- Doesn't catch logic errors or edge cases
- Can't prevent all unintended behavior

**Mitigation**: Thoroughly test tools independently before deployment

## Next Steps

- **[Tools](tools.md)** - Learn how to create tools
- **[Rich Tools](rich-tools.md)** - Enhanced approval UI with RichToolOutput
- **[Defining Agents](defining-agents.md)** - Complete agent configuration reference
- **[Server Mode](../backends/server.md)** - Implement custom approval UIs via RPC
