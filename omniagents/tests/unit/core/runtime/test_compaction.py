from omniagents.core.runtime.compaction import (
    build_resume_protocol_block,
    extract_tool_events,
    format_tool_events_block,
)


def test_extract_tool_events_and_format_block():
    history = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "content": "ok", "tool_call_id": "call-1"},
    ]

    events = extract_tool_events(history)

    assert len(events) == 2
    rendered = format_tool_events_block(events)
    assert "read_file" in rendered
    assert '"tool_call_id": "call-1"' in rendered


def test_build_resume_protocol_block_lists_recent_files():
    history = [
        {"role": "user", "content": "fix the bug"},
        {"role": "user", "content": "continue"},
    ]
    tool_events = [{"name": "read_file", "file_path": "/tmp/demo.py"}]

    block = build_resume_protocol_block(history=history, tool_events=tool_events)

    assert "Resume Protocol (do this first):" in block
    assert "/tmp/demo.py" in block
