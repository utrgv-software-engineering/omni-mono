# Voice Backend - Tool Arguments Not Displaying

## ✅ FIXED

**Status**: Issue resolved as of this commit.

**Solution**: Cache tool call arguments from raw model events and use them to enrich tool_start events.

## Problem Statement

When using the voice backend (`omniagents run --mode voice`), tool calls were detected and displayed, but the **arguments were not showing up**. The tool name and results were visible, but the JSON arguments passed to the tool were missing.

### Current Behavior

```
🔧 [Calling tool: get_current_weather]
✓ [Tool completed: get_current_weather]
  Result: I don't have current weather data for Edinburgh...
```

### Expected Behavior

```
🔧 [Calling tool: get_current_weather]
   Arguments: {
     "location": "Edinburgh"
   }
✓ [Tool completed: get_current_weather]
  Result: I don't have current weather data for Edinburgh...
```

## Architecture Overview

### Event Flow

```
OpenAI Realtime API
    ↓ (WebSocket events)
agents SDK (openai-agents)
    ↓ (RealtimeToolStart event)
map_realtime_session_event_to_rpc()
    ↓ (RealtimeToolStartEvent)
RealtimeService._stream_session_events()
    ↓ (JSON-RPC notification)
VoiceClient.realtime_event()
    ↓ (Display)
Terminal Output
```

## Relevant Files

### OmniAgents Codebase

#### RPC Protocol Implementation
- **`omniagents/rpc/realtime_events.py:286-317`** - Maps SDK events to RPC events
  - `map_realtime_session_event_to_rpc()` function
  - Creates `RealtimeToolStartEvent` from SDK's `RealtimeToolStart`
  - Currently tries to extract arguments from `info.context.current_tool_call`

- **`omniagents/rpc/realtime_service.py:90-118`** - Streams events from session to client
  - `_stream_session_events()` method
  - Calls `event.to_dict()` and sends via JSON-RPC

- **`omniagents/rpc/realtime_session.py:219-241`** - Wraps agents SDK session
  - `stream_events()` generator
  - Calls `map_realtime_session_event_to_rpc()`

#### Voice Client
- **`omniagents/backends/voice/client.py:89-103`** - Displays tool events
  - `ClientNotificationHandler.realtime_event()` method
  - Handles `realtime_tool_start` and `realtime_tool_end` events
  - Extracts `tool_name` and `arguments` from params dict

- **`examples/realtime_voice_agent/voice_client.py:90-104`** - Standalone example
  - Same logic as voice backend client
  - Pretty-prints JSON arguments

### OpenAI Agents SDK (External)

Located in: `/Users/ericmartinez/.pyenv/versions/3.13.0/lib/python3.13/site-packages/agents/`

#### Event Definitions
- **`agents/realtime/events.py:65-76`** - `RealtimeToolStart` dataclass
  ```python
  @dataclass
  class RealtimeToolStart:
      agent: RealtimeAgent
      tool: Tool
      info: RealtimeEventInfo
      type: Literal["tool_start"] = "tool_start"
  ```
  - **Does NOT contain arguments field**
  - Only has `tool` (just the Tool definition with name)
  - Has `info` with RunContextWrapper

- **`agents/realtime/events.py:16-19`** - `RealtimeEventInfo` dataclass
  ```python
  @dataclass
  class RealtimeEventInfo:
      context: RunContextWrapper
  ```

#### Tool Definitions
- **`agents/tool.py:145-191`** - `FunctionTool` dataclass
  - Has `name`, `description`, `params_json_schema`
  - Does NOT have `arguments` field (that's the schema, not the actual call arguments)

- **`agents/tool.py:376-385`** - Tool type union
  - Union of FunctionTool, FileSearchTool, WebSearchTool, ComputerTool, etc.

#### Realtime Items (Where Arguments ARE Stored)
- **`agents/realtime/items.py:158-186`** - `RealtimeToolCallItem`
  ```python
  class RealtimeToolCallItem(BaseModel):
      item_id: str
      call_id: str | None
      type: Literal["function_call"] = "function_call"
      status: Literal["in_progress", "completed"]
      arguments: str  # ← THE ARGUMENTS ARE HERE!
      name: str
      output: str | None = None
  ```
  - **This is where the actual tool call arguments are stored**
  - Part of conversation history items

- **`agents/realtime/events.py:178-187`** - `RealtimeHistoryUpdatedEvent`
  ```python
  @dataclass
  class RealtimeHistoryUpdatedEvent:
      history: list[RealtimeItem]  # Contains RealtimeToolCallItem
      info: RealtimeEventInfo
      type: Literal["history_updated"] = "history_updated"
  ```

#### Realtime Implementation
- **`agents/realtime/openai_realtime.py`** - Main realtime session implementation
  - Handles WebSocket connection to OpenAI
  - Emits session events (including `tool_start`)

## Investigation Details

### What We Know

1. **Arguments are NOT in the tool_start event** - The SDK's `RealtimeToolStart` event only contains:
   - `agent`: The agent object
   - `tool`: The Tool definition (just name, schema, etc.)
   - `info`: Context wrapper
   - **No arguments field**

2. **Arguments ARE in RealtimeToolCallItem** - The actual arguments are stored in the conversation history as `RealtimeToolCallItem` objects with:
   - `arguments`: JSON string of the arguments
   - `call_id`: The call ID
   - `name`: Tool name
   - `status`: in_progress or completed

3. **Attempts Made**:
   - ✗ Tried extracting from `tool_obj` attributes - Tool object doesn't have arguments
   - ✗ Tried extracting from `event` attributes - Event doesn't have arguments
   - ✗ Tried extracting from `info.context.current_tool_call` - Attribute doesn't exist or isn't populated

### Possible Solutions

#### Option 1: Track History and Match by Tool Name/Timing
When we receive a `tool_start` event:
1. Look at the conversation history for recent `RealtimeToolCallItem` entries
2. Find the most recent in-progress tool call with matching name
3. Extract the `arguments` field from that item

**Files to modify:**
- `omniagents/rpc/realtime_session.py` - Pass history to event mapper
- `omniagents/rpc/realtime_events.py` - Accept history in mapper, look up tool call

**Concerns:**
- Race conditions if multiple tools called simultaneously
- Requires maintaining conversation state

#### Option 2: Listen for history_updated Events
When `history_updated` event fires:
1. Parse the history for `RealtimeToolCallItem` entries
2. When a new tool call item appears with `status: "in_progress"`, emit our own event
3. Cache the arguments for when `tool_start` event arrives

**Files to modify:**
- `omniagents/rpc/realtime_events.py` - Add handler for history_updated
- `omniagents/rpc/realtime_session.py` - Cache tool call state

**Concerns:**
- Ordering of events (does history_updated come before tool_start?)
- Complexity of state management

#### Option 3: Intercept Raw Model Events
The SDK may emit lower-level model events that contain the function call.

**Files to investigate:**
- `agents/realtime/model_events.py` - Raw model events from OpenAI API
- Look for `response.function_call_arguments.delta` or similar

**Files to modify:**
- `omniagents/rpc/realtime_events.py` - Add handler for raw model events

#### Option 4: Query Context State
The `RunContextWrapper` in `info.context` may have methods to query current state.

**Need to investigate:**
- `agents/run_context.py` - RunContextWrapper implementation
- Check if there's a way to get current tool call from context
- Look for `get_current_tool_call()` or similar methods

## Testing

To reproduce the issue:

```bash
cd examples/realtime_voice_agent
export OPENAI_API_KEY="sk-..."
omniagents run --mode voice
```

Then speak: "What's the weather in Edinburgh?"

Expected: Should show arguments in tool call display
Actual: Arguments are empty/not displayed

## Next Steps

1. **Investigate RunContextWrapper** - Check what attributes/methods are available
   - File: `agents/run_context.py`
   - Look for current tool call state

2. **Check history_updated event timing** - Add debug logging to see:
   - When does `history_updated` fire relative to `tool_start`?
   - Does the history contain the tool call item before `tool_start` fires?

3. **Look at raw model events** - Check if there are lower-level events:
   - File: `agents/realtime/model_events.py`
   - Look for function call argument events

4. **Enable debug mode** - Add environment variable to print full event structure:
   ```bash
   export OMNI_DEBUG_EVENTS=1
   omniagents run --mode voice --debug
   ```

## Related Issues

- Tool results DO show up correctly (from `tool_end` event which has `output` field)
- Tool names DO show up correctly (from `tool.name`)
- Only arguments are missing (not in SDK event structure)

## Root Cause Analysis

The fundamental issue was that the OpenAI agents SDK's `RealtimeToolStart` event **by design** does not include the arguments. The code incorrectly assumed that `info.context.current_tool_call` would exist on the `RunContextWrapper`, but this attribute doesn't exist.

However, the arguments ARE available in `RealtimeModelToolCallEvent` (raw model events), which arrive before `tool_start` events with complete information including:
- `name`: Tool name
- `call_id`: Call identifier
- `arguments`: JSON string of arguments

## The Solution

The fix mirrors how non-realtime mode works:
- **Non-realtime**: Extracts arguments from `tool_call_item.to_input_item()` which has all data
- **Realtime**: Extracts arguments from raw model events (`function_call` type) which have all data

### Implementation

**Modified Files:**
1. `omniagents/rpc/realtime_session.py:219-265` - Added caching in `stream_events()`
2. `omniagents/rpc/realtime_events.py:210-342` - Updated mapper to use cache
3. `tests/unit/rpc/test_realtime_events.py` - Added test for cache functionality

**How it works:**

1. **Cache tool arguments from raw model events** (`realtime_session.py`):
   ```python
   tool_call_cache = {}  # Key: (tool_name, call_id) -> arguments string

   if event.type == "raw_model_event":
       data = getattr(event, "data", None)
       if data and getattr(data, "type", None) == "function_call":
           tool_name = getattr(data, "name", None)
           call_id = getattr(data, "call_id", None)
           arguments = getattr(data, "arguments", "")
           if tool_name and call_id:
               tool_call_cache[(tool_name, call_id)] = arguments
   ```

2. **Enrich tool_start events with cached arguments** (`realtime_events.py`):
   ```python
   elif event_type == "tool_start":
       tool_name = getattr(tool_obj, "name", "unknown")

       # Look up arguments from cache
       if tool_call_id:
           cache_key = (tool_name, tool_call_id)
           arguments = tool_call_cache.get(cache_key, "")

       # Fallback: match by tool name alone
       if not arguments:
           for (cached_name, cached_id), cached_args in tool_call_cache.items():
               if cached_name == tool_name:
                   arguments = cached_args
                   tool_call_id = cached_id
                   break
   ```

3. **Clean up cache after tool completes** (`realtime_session.py`):
   ```python
   if rpc_event.type == "realtime_tool_end":
       cache_key = (rpc_event.tool_name, rpc_event.tool_call_id)
       tool_call_cache.pop(cache_key, None)
   ```

### Benefits

- **Consistent with omniagents architecture**: Mirrors how non-realtime extracts data from available sources
- **No breaking changes**: Clients still receive `realtime_tool_start` events with same structure
- **Clean data flow**: Raw events arrive first → cache → session events enriched with cache
- **Memory efficient**: Cache entries cleaned up after tool completes
