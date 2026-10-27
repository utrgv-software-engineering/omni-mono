"""The guardian's transcript keeps the user's messages however long the run.

It showed only the last 40 entries, so on a long run the request that
authorized an action (a dispatch asking for a pull request ready for review)
had scrolled out of view, and the guardian denied the action for want of a
user request.
"""

import asyncio
from types import SimpleNamespace

from omniagents.core.agents.transcript_compaction import (
    MAX_TRANSCRIPT_ENTRIES,
    compact_transcript,
)


def _run(items):
    service = SimpleNamespace()

    async def history(session_id):
        return items

    service.get_session_history = history
    return asyncio.run(compact_transcript(service, SimpleNamespace(id="s")))


def _tool_traffic(n):
    out = []
    for i in range(n):
        out.append(
            {"type": "function_call", "name": "execute_bash", "arguments": f"step {i}"}
        )
        out.append({"type": "function_call_output", "output": f"ok {i}"})
    return out


def test_the_task_survives_a_long_run():
    task = {
        "role": "user",
        "content": "Deliver #12 as a pull request ready for review.",
    }
    text = _run([task, *_tool_traffic(MAX_TRANSCRIPT_ENTRIES)])
    assert "Deliver #12 as a pull request ready for review." in text
    assert text.index("ready for review") < text.index(
        f"step {MAX_TRANSCRIPT_ENTRIES - 1}"
    )
    assert "step 0" not in text  # old tool traffic still leaves the window


def test_later_user_messages_are_kept_in_order():
    first = {"role": "user", "content": "Implement #12."}
    later = {"role": "user", "content": "Also do not post comments."}
    text = _run([first, *_tool_traffic(30), later, *_tool_traffic(30)])
    assert text.index("Implement #12.") < text.index("Also do not post comments.")


def test_short_runs_are_unchanged():
    items = [{"role": "user", "content": "hi"}, *_tool_traffic(2)]
    text = _run(items)
    assert text.startswith("user: hi")
    assert "earlier" not in text
