# Image Tools Documentation

## Overview

OmniAgents supports reading and returning images using the OpenAI Agents SDK's vision capabilities. This allows AI models to "see" and analyze images through the `read_image` tool.

## How It Works

The `read_image` tool leverages the OpenAI Agents SDK's structured output types to return images to the LLM:

1. **Read image file**: The tool reads an image file from disk
2. **Convert to base64**: The image is encoded as a base64 data URL
3. **Return structured output**: Returns a list containing:
   - `ToolOutputText`: A text description of the image (file size, format)
   - `ToolOutputImage`: The actual image data for vision analysis

## Available Types from OpenAI Agents SDK

The agents SDK provides several output types for tools:

### ToolOutputText
```python
from agents.tool import ToolOutputText

output = ToolOutputText(
    type="text",
    text="Some text content"
)
```

### ToolOutputImage
```python
from agents.tool import ToolOutputImage

output = ToolOutputImage(
    type="image",
    image_url="data:image/png;base64,iVBORw...",  # Data URL or HTTP URL
    # OR
    file_id="file-abc123",  # Previously uploaded file
    detail="auto"  # Optional: "low", "high", or "auto"
)
```

### ToolOutputFileContent
```python
from agents.tool import ToolOutputFileContent

output = ToolOutputFileContent(
    type="file",
    file_data="base64_encoded_data",  # Base64 encoded file
    # OR
    file_url="https://example.com/file.pdf",
    # OR
    file_id="file-abc123",
    filename="document.pdf"  # Optional filename
)
```

## The read_image Tool

### Function Signature

```python
@function_tool
def read_image(
    ctx: RunContextWrapper[Any],
    image_path: str,
    detail: Optional[str] = None,
) -> List[Union[ToolOutputText, ToolOutputImage]]
```

### Parameters

- **image_path**: Path to the image file (resolved relative to workspace root)
- **detail**: Optional detail level for image processing:
  - `"low"`: Faster, lower resolution analysis
  - `"high"`: Slower, higher resolution analysis
  - `"auto"`: Let the model decide (default)

### Return Value

Returns a list of structured outputs:
1. `ToolOutputText`: Description of the image (filename, size, format)
2. `ToolOutputImage`: The actual image encoded as a data URL

### Supported Formats

- PNG (.png)
- JPEG (.jpg, .jpeg)
- GIF (.gif)
- WebP (.webp)
- BMP (.bmp)
- SVG (.svg)

## Usage Examples

### Basic Usage

```python
from omniagents.builtin.tools import read_image
from agents import Agent, AgentBuilder

agent = AgentBuilder(
    name="Image Analyzer",
    instructions="You can analyze images using the read_image tool.",
    tools=[read_image],
    model="gpt-4o",  # Vision-capable model required
).build()

# The agent can now respond to requests like:
# "Please analyze the image at ./diagram.png"
# "What's in screenshot.jpg?"
```

### YAML Configuration

```yaml
name: "Image Analyzer"
instructions_file: "instructions.txt"
model: "gpt-4o"
tools:
  - read_image
```

### High Detail Analysis

When you need detailed analysis of complex images (diagrams, charts, dense text):

```python
# The agent will use:
# read_image("./complex_diagram.png", detail="high")
```

### Low Detail for Simple Images

For simple images or when speed is important:

```python
# The agent will use:
# read_image("./simple_photo.jpg", detail="low")
```

## How It Integrates with OpenAI Agents SDK

The tool follows the OpenAI Agents SDK conventions:

1. **Decorated with @function_tool**: Automatically creates a `FunctionTool` with JSON schema
2. **Returns structured types**: Returns `ToolOutputImage` and `ToolOutputText` directly
3. **Vision model support**: Works with any vision-capable model (GPT-4o, GPT-4 Turbo, etc.)

### Why Return a List?

The tool returns `[ToolOutputText, ToolOutputImage]` to provide both:

- **Context for the LLM**: Text description helps the model understand what was loaded
- **Visual content**: The actual image for vision analysis

The OpenAI Agents SDK automatically handles this list and presents both components to the model appropriately.

## Implementation Details

### Image Encoding

Images are converted to base64 data URLs:

```
data:image/png;base64,iVBORw0KGgoAAAANSUhEUgA...
```

This allows images to be transmitted inline without requiring separate file uploads.

### Path Resolution

The tool uses the workspace root from the context to resolve relative paths:

```python
# Resolves relative to workspace_root
read_image("./images/photo.jpg")

# Absolute paths work too
read_image("/Users/me/Pictures/photo.jpg")
```

### Error Handling

The tool handles errors gracefully by returning `ToolOutputText` with error messages:

- File not found
- Path is not a file
- Read errors

## Differences from RichToolOutput

Unlike other omniagents tools that use `RichToolOutput`, the `read_image` tool:

1. Returns **structured SDK types directly** (`ToolOutputImage`)
2. Does not wrap in `RichToolOutput` since the image itself is the output
3. Still uses `@function_tool` from omniagents for consistency

This is because images are first-class content types in the SDK, not just UI metadata.

## Testing

See `tests/unit/builtin/tools/test_image_tools.py` for comprehensive tests including:

- Successful image reading
- Detail level control
- Error handling (file not found, not a file)
- Multiple format support

## Future Enhancements

Potential future improvements:

1. **Batch image reading**: Read multiple images in one call
2. **Image manipulation**: Resize, crop, convert formats
3. **URL support**: Fetch images from URLs directly
4. **File upload integration**: Support for OpenAI file uploads
5. **Video frame extraction**: Extract frames from videos
6. **OCR pre-processing**: Extract text before sending to model

## Related Documentation

- OpenAI Agents SDK: https://github.com/openai/openai-agents-python
- OpenAI Vision: https://platform.openai.com/docs/guides/vision
- RICH_OUTPUT.md: Documentation for omniagents rich output system
