# Rich Tool Output Format

## Overview

OmniAgents supports rich tool outputs through the `RichToolOutput` class and `rich_function_tool` decorator. This system separates tool outputs into two components: text for the LLM context and structured metadata for enhanced UI rendering, enabling backends to create more informative displays without polluting the LLM's context window.

## How It Works

Tools decorated with `@rich_function_tool` return `RichToolOutput` instances containing both LLM-focused text and UI metadata. The runtime bridge automatically detects these rich outputs, extracts the metadata, and includes it in the tool result events sent to clients. When stringified (via `__str__()`), only the LLM text is returned, keeping the context window clean.

## Implementation Components

### 1. RichToolOutput Class

Located in `omniagents/core/tools/discovery.py`:

```python
class RichToolOutput:
    def __init__(self, llm_output: str, ui_metadata: Dict[str, Any] = None):
        self.llm_output = llm_output      # Text for LLM context
        self.ui_metadata = ui_metadata     # Metadata for UI rendering
    
    def __str__(self):
        return self.llm_output  # Only returns LLM text when stringified
```

### 2. rich_function_tool Decorator

```python
from omniagents.core.tools import rich_function_tool, RichToolOutput

@rich_function_tool
def my_tool(param: str) -> RichToolOutput:
    # Process the tool logic
    result_data = process_something(param)
    
    # Create UI metadata
    ui_metadata = {
        "value": result_data,
        "display_type": "appropriate_type",
        "summary": "Brief description",
        "preview": "Preview text",
        "truncated": False,
        "metadata": {
            # Type-specific fields
        }
    }
    
    # Return RichToolOutput
    return RichToolOutput(llm_text, ui_metadata)
```

## UI Metadata Structure

### Standard Fields

Every `ui_metadata` dictionary should follow this pattern:

```python
ui_metadata = {
    "value": actual_data,           # The actual result/data
    "display_type": "type_name",    # Type identifier for UI rendering
    "summary": "brief description", # One-line summary
    "preview": "preview text",      # Preview text (often truncated)
    "truncated": bool,              # Whether preview is truncated
    "metadata": {                   # Additional type-specific metadata
        # type-specific fields
    }
}

```

## Display Types and Patterns

### 1. command
**Purpose**: Shell command execution results  
**Used by**: `execute_bash`  
**Metadata fields**:
- `command`: The executed command
- `stdout`: Standard output
- `stderr`: Standard error output
- `exit_code`: Command exit code
- `success`: Boolean success indicator
- `line_count`: Total number of output lines
- `preview_lines`: Number of lines in preview

### 2. error
**Purpose**: Error conditions in any tool  
**Used by**: All tools on error  
**Metadata fields**:
- `error_type`: Type of error (e.g., "network_error", "file_not_found")
- `error`: Error message/description
- Additional context fields specific to the error

### 3. search_results
**Purpose**: Search and listing operations  
**Used by**: `glob_files`, `grep_files`, `web_search`, `youtube_search`, `scholar_search`  
**Metadata fields**:
- `query`: Search query/pattern
- `result_count`: Total number of results
- `match_count`: Number of matches (for grep)
- `file_count`: Number of files (for file searches)
- `search_type`: Type of search performed
- `scope`: Search scope/path
- Additional search-specific filters

### 4. file_content
**Purpose**: File reading operations  
**Used by**: `read_file`  
**Metadata fields**:
- `file_path`: Path to the file
- `line_count`: Number of lines read
- `file_size`: Size in bytes
- `language`: Detected programming language
- `start_line`: Starting line number (if partial read)
- `preview_lines`: Number of lines in preview

### 5. file_edit
**Purpose**: File modification operations  
**Used by**: `edit_file`, `write_file`  
**Metadata fields**:
- `file_path`: Path to the file
- `operation`: Type of operation ("edit", "write", "append")
- `changes_made`: Number of replacements (for edit)
- `bytes_written`: Number of bytes written
- `line_count`: Number of lines written

### 6. directory_listing
**Purpose**: Directory content listings  
**Used by**: `list_directory`  
**Metadata fields**:
- `path`: Directory path
- `file_count`: Number of files
- `dir_count`: Number of directories
- `total_count`: Total items
- `recursive`: Whether listing is recursive
- `preview_count`: Number of items in preview

### 7. web_content
**Purpose**: Web page fetching  
**Used by**: `web_fetch`  
**Metadata fields**:
- `url`: Source URL
- `title`: Page title (if available)
- `content_type`: MIME type
- `size`: Content size in bytes
- `meta_tags`: Extracted meta tags
- `link_count`: Number of links found
- `extracted_text`: Whether text was extracted

### 8. conversion
**Purpose**: File format conversions  
**Used by**: `convert_to_markdown`  
**Metadata fields**:
- `success`: Conversion success indicator
- `file_path`: Source file path
- `source_type`: Source file type
- `content_length`: Length of converted content

### 9. download
**Purpose**: File downloads  
**Used by**: `download_file`  
**Metadata fields**:
- `success`: Download success indicator
- `file_path`: Local path where file was saved
- `url`: Source URL
- `content_type`: MIME type
- `file_size`: Downloaded file size
- `is_temporary`: Whether saved to temp location

## Runtime Bridge Integration

The runtime bridge (`omniagents/core/runtime/bridge.py`) automatically handles RichToolOutput:

```python
# In _stream_event_payload function
if item.type == "tool_call_output_item":
    output = getattr(item, "output", "")
    ui_metadata = {}
    
    # Check if output is a RichToolOutput instance
    from omniagents.core.tools import RichToolOutput
    if isinstance(output, RichToolOutput):
        ui_metadata = output.ui_metadata
        # String conversion will use __str__ which returns only llm_output
    
    payload = {
        "run_id": run_id,
        "tool": getattr(item, "name", "<tool>"),
        "output": str(output),  # Calls __str__ for RichToolOutput
        "metadata": ui_metadata  # Included only if not empty
    }
```

## Backend Implementation Guide

### 1. Receiving Tool Results

Backends receive tool results via the `tool_result` event which includes metadata when available:

```python
# Example tool_result event structure
{
    "tool": "execute_bash",
    "output": "Command executed successfully:\nHello World",
    "call_id": "call_123",
    "metadata": {  # Present only for rich outputs
        "value": "Hello World",
        "display_type": "command",
        "summary": "$ echo 'Hello World'",
        "preview": "Hello World",
        "truncated": false,
        "metadata": {
            "command": "echo 'Hello World'",
            "exit_code": 0,
            "success": true
        }
    }
}
```

### 2. Handling Rich Metadata

Check for the presence of metadata in tool results:

```python
def handle_tool_result(event):
    tool_name = event.get("tool")
    output = event.get("output")
    metadata = event.get("metadata")  # May be None or empty dict
    
    if metadata and metadata.get("display_type"):
        # Rich output with metadata
        return format_rich_output(metadata)
    else:
        # Plain text output
        return format_plain_output(output)
```

### 3. Bubble Tea Backend Implementation

The Bubble Tea backend (`omniagents/backends/bubbletea/cmd/omni-tui/`) demonstrates rich output handling:

#### Event Structure (events.go)
```go
type ToolResultEvent struct {
    Tool     string                 `json:"tool"`
    Output   string                 `json:"output"`
    CallID   string                 `json:"call_id"`
    Metadata map[string]interface{} `json:"metadata,omitempty"`
}
```

#### Rich Metadata Parsing (ui.go)
```go
type RichMetadata struct {
    DisplayType string                 `json:"display_type"`
    Summary     string                 `json:"summary"`
    Preview     string                 `json:"preview,omitempty"`
    Truncated   bool                   `json:"truncated"`
    Metadata    map[string]interface{} `json:"metadata,omitempty"`
}

func parseRichMetadata(metadata map[string]interface{}) *RichMetadata {
    if metadata == nil {
        return nil
    }
    // Extract fields from metadata map
    rich := &RichMetadata{}
    // ... parsing logic
    return rich
}
```

#### Display Examples

**Command Output**:
```
⏺ execute_bash(command="npm run build")
  └─ $ npm run build
     Building project...
     ✓ Compiled 42 modules
     ... +15 more lines (exit: 0)
```

**File Content**:
```
⏺ read_file(path="config.py")
  └─ Read 250 lines from config.py
     # Configuration file
     import os
     from pathlib import Path
     ... +240 more lines
```

**Search Results**:
```
⏺ grep_files(pattern="TODO", include="*.py")
  └─ Found 15 matches for 'TODO' in 5 files
     src/main.py: 3 matches
       Line 42: # TODO: implement caching
     src/utils.py: 2 matches
       Line 101: # TODO: add error handling
     ... +3 more files
```

## Creating a Rich Tool

Here's a complete example of implementing a rich tool:

```python
from omniagents.core.tools import rich_function_tool, RichToolOutput
import subprocess

@rich_function_tool
def run_tests(test_path: str, verbose: bool) -> RichToolOutput:
    """Run unit tests and return formatted results."""
    try:
        # Run the tests
        cmd = f"pytest {test_path}" + (" -v" if verbose else "")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        
        # Parse output for summary
        output = result.stdout + result.stderr
        lines = output.split('\n')
        
        # Extract test summary
        passed = failed = 0
        for line in lines:
            if "passed" in line:
                # Parse pytest summary line
                parts = line.split()
                for i, part in enumerate(parts):
                    if "passed" in part and i > 0:
                        passed = int(parts[i-1])
                    if "failed" in part and i > 0:
                        failed = int(parts[i-1])
        
        # Create LLM output
        if result.returncode == 0:
            llm_output = f"Tests passed: {passed} tests executed successfully"
        else:
            llm_output = f"Tests failed: {passed} passed, {failed} failed\n{output}"
        
        # Create UI metadata
        ui_metadata = {
            "value": output,
            "display_type": "command",
            "summary": f"pytest {test_path}: {passed} passed, {failed} failed",
            "preview": '\n'.join(lines[:10]),
            "truncated": len(lines) > 10,
            "metadata": {
                "command": cmd,
                "exit_code": result.returncode,
                "success": result.returncode == 0,
                "tests_passed": passed,
                "tests_failed": failed,
                "line_count": len(lines)
            }
        }
        
        return RichToolOutput(llm_output, ui_metadata)
        
    except Exception as e:
        error_msg = f"Error running tests: {str(e)}"
        ui_metadata = {
            "value": error_msg,
            "display_type": "error",
            "summary": "Test execution failed",
            "preview": str(e),
            "truncated": False,
            "metadata": {
                "error_type": "execution_error",
                "test_path": test_path,
                "error": str(e)
            }
        }
        return RichToolOutput(error_msg, ui_metadata)
```

## Benefits

1. **Clean Context Window**: LLM only sees relevant text, not UI metadata
2. **Rich UI Rendering**: Backends can create informative, type-specific displays
3. **Consistent Pattern**: All tools follow the same metadata structure
4. **Error Handling**: Built-in error display type for consistent error presentation
5. **Progressive Enhancement**: Backends without rich support still see plain text output

## Testing Rich Output

To test rich output in your tools:

```python
# Test the tool directly
from my_tools import my_rich_tool

result = my_rich_tool("test_param")
print(f"LLM sees: {result}")  # Uses __str__()
print(f"UI metadata: {result.ui_metadata}")

# Test in an agent
from omniagents.core.agent_builder import create_agent_from_yaml

agent = create_agent_from_yaml("config.yaml")
# Tools with rich output will automatically have metadata extracted
```

## Migration Guide

### For Tool Developers

1. **Import the decorators and class**:
   ```python
   from omniagents.core.tools import rich_function_tool, RichToolOutput
   ```

2. **Change decorator from `@function_tool` to `@rich_function_tool`**

3. **Return `RichToolOutput` instead of plain string**:
   ```python
   # Before
   return f"Result: {data}"
   
   # After
   ui_metadata = {
       "value": data,
       "display_type": "appropriate_type",
       "summary": "Brief description",
       "preview": str(data)[:500],
       "truncated": len(str(data)) > 500,
       "metadata": {}
   }
   return RichToolOutput(f"Result: {data}", ui_metadata)
   ```

### For Backend Developers

1. **Check for metadata in tool_result events**
2. **Parse metadata if present, otherwise use plain output**
3. **Implement type-specific rendering based on `display_type`**
4. **Add truncation indicators when `truncated` is true**

## FAQ

### Q: What happens if a backend doesn't handle rich metadata?
A: The backend still receives the plain text output in the `output` field. The metadata is simply ignored.

### Q: Can I mix rich and non-rich tools?
A: Yes, the system handles both transparently. Tools without rich output simply don't include metadata.

### Q: How does this affect the LLM's context?
A: The LLM only sees the `llm_output` text, keeping the context clean and focused.

### Q: Should all tools use rich output?
A: Tools that benefit from enhanced UI display should use it. Simple tools that return plain text may not need it.

### Q: What about performance?
A: The overhead is minimal - just detecting if an object is a `RichToolOutput` instance during streaming.