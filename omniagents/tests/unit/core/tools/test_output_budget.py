import json
from types import SimpleNamespace

import pytest
from agents import Agent, RunContextWrapper, function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext

from omniagents.core.tools import RichToolOutput
from omniagents.core.tools import output_budget as output


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    return ToolContext(
        context={"session_id": "owner"},
        tool_name="test",
        tool_call_id="call",
        tool_arguments="{}",
    )


@pytest.mark.asyncio
async def test_long_line_under_budget_is_unchanged(context):
    value = RichToolOutput("a" * 25000 + "MATCH", {"preview": "a"})
    assert await output.bound_output(value, context) is value


@pytest.mark.asyncio
async def test_full_text_saved_before_preview_and_searchable(context):
    text = "a" * 90000 + "needle" + "b" * 90000
    result = await output.bound_output(
        RichToolOutput(text), context, ("product", "agent")
    )
    assert len(str(result).encode()) <= output.MAX_OUTPUT_BYTES
    info = result.ui_metadata["output_retention"]
    assert info["status"] == "saved"
    # Reload through disk, independent of the original result/process object.
    page = output.read_output(
        ("product", "agent"), "owner", info["reference"], query="needle"
    )
    assert page["match_offset"] == 90000
    assert "needle" in page["text"]
    assert page["next_search_offset"] == 90006
    collected = ""
    offset = 0
    while True:
        page = output.read_output(
            ("product", "agent"), "owner", info["reference"], offset
        )
        collected += page["text"]
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert collected == text
    for scope, session in [
        (("product", "agent"), "other"),
        (("product", "other"), "owner"),
    ]:
        with pytest.raises(ValueError, match="unavailable"):
            output.read_output(scope, session, info["reference"])
    with pytest.raises(ValueError):
        output.read_output(("product", "agent"), "owner", "../escape")


@pytest.mark.asyncio
async def test_failure_notice_does_not_claim_retention(context, monkeypatch):
    def fail(*args):
        raise OSError("sensitive filesystem detail")

    monkeypatch.setattr(output, "save_output", fail)
    result = await output.bound_output("x" * 80000, context)
    assert result.ui_metadata["output_retention"]["status"] == "unavailable"
    assert "Saving the full text failed" in str(result)
    assert "sensitive" not in str(result)
    assert len(str(result).encode()) <= output.MAX_OUTPUT_BYTES


@pytest.mark.asyncio
async def test_structured_output_preserves_media_and_budgets_all_text(context):
    media = ToolOutputImage(image_url="data:image/png;base64," + "a" * 80000)
    assert await output.bound_output(media, context) is media
    result = await output.bound_output(
        [
            ToolOutputText(text="é" * 40000),
            media,
            {"type": "text", "text": "z" * 80000},
        ],
        context,
    )
    assert result[1] is media
    assert (
        sum(
            len(t.encode())
            for item in result
            if isinstance(t := output._text_part(item), str)
        )
        <= output.MAX_OUTPUT_BYTES
    )
    assert result.ui_metadata["output_retention"]["status"] == "saved"


@pytest.mark.asyncio
async def test_smaller_shell_budget_is_honored(context):
    result = await output.bound_output(
        RichToolOutput("x" * 90000, max_output_chars=2000), context
    )
    assert len(str(result).encode()) <= 2000


@pytest.mark.asyncio
async def test_dynamic_tools_and_reader_share_the_boundary(context):
    @function_tool
    async def custom() -> str:
        """Return a large result."""
        return "x" * 80000

    agent = Agent(name="test", tools=[custom])

    async def mcp(ctx):
        from dataclasses import replace

        return [replace(custom, name="mcp_example")]

    agent.get_mcp_tools = mcp
    output.install_output_budget(agent, ("product", "agent"))
    tools = {t.name: t for t in await agent.get_all_tools(context)}
    for name in ("custom", "mcp_example"):
        result = await tools[name].on_invoke_tool(context, "{}")
        ref = result.ui_metadata["output_retention"]["reference"]
        page = await tools["read_tool_output"].on_invoke_tool(
            context,
            json.dumps({"reference": ref, "offset": 100, "limit": 1000, "query": None}),
        )
        assert json.loads(page)["text"] == "x" * 1000
    # Wrapping must not mutate a shared FunctionTool instance.
    assert len(await custom.on_invoke_tool(context, "{}")) == 80000


@pytest.mark.asyncio
async def test_thread_rpc_uses_authorized_session_scope(context):
    from omniagents.builtin.server_functions.tool_outputs import _invoke

    ref = output.save_output(("product", "agent"), "owner", "persisted text")
    service = SimpleNamespace(
        _session_manager=SimpleNamespace(_project_slug="product", _agent_slug="agent")
    )
    assert (await _invoke(service, SimpleNamespace(id="owner"), {"reference": ref}))[
        "text"
    ] == "persisted text"
    with pytest.raises(ValueError, match="unavailable"):
        await _invoke(service, SimpleNamespace(id="other"), {"reference": ref})


def test_session_purge_removes_saved_outputs(monkeypatch, tmp_path):
    from omniagents.core.session.retention import purge_session_derivatives
    from omniagents.core.tools.output_budget import save_output, read_output

    monkeypatch.setenv("OMNIAGENTS_HISTORY_DB", str(tmp_path / "sessions.db"))
    reference = save_output((None, None), "purged", "retained")
    other = save_output((None, None), "other", "keep")
    purge_session_derivatives("purged")
    with pytest.raises(ValueError, match="unavailable"):
        read_output((None, None), "purged", reference)
    assert read_output((None, None), "other", other)["text"] == "keep"


def test_search_across_chunk_boundary_with_small_page(context):
    text = "a" * 65534 + "needle" + "b" * 90000
    ref = output.save_output((None, None), "owner", text)
    page = output.read_output((None, None), "owner", ref, query="needle", limit=6)
    assert page["text"] == "needle"
    assert page["match_offset"] == 65534
