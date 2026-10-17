"""QuickJS orchestration entrypoint; Python retains authority over every call."""

import json
from importlib.util import find_spec
from importlib.metadata import version, PackageNotFoundError
from typing import Any
from agents import RunContextWrapper
from omniagents.core.tools import function_tool
from omniagents.core.orchestration.dispatch import Dispatcher
from omniagents.core.orchestration import cells


def format_result(result):
    from agents.tool import ToolOutputText

    media = result.get("media", [])
    text = json.dumps(
        {k: v for k, v in result.items() if k != "media"}, ensure_ascii=False
    )
    return [ToolOutputText(text=text), *media] if media else text


def runtime_available(context=None, agent=None):
    if find_spec("omniagents._quickjs") is not None:
        return True
    try:
        return version("quickjs-ng") == "0.17.0.1"
    except PackageNotFoundError:
        return False


def orchestration_error(ctx, error):
    from agents.tool import default_tool_error_function
    from omniagents.core.orchestration.runtime import TRIPWIRES

    if isinstance(error, TRIPWIRES):
        raise error
    return default_tool_error_function(ctx, error)


@function_tool(is_enabled=runtime_available, failure_error_function=orchestration_error)
async def exec_javascript(
    ctx: RunContextWrapper[Any], code: str, yield_time_ms: int = 1000
) -> Any:
    """Run JavaScript with top-level await to inspect and chain tools without flooding context.

    Discover names and schemas with await tools.list(). Invoke with
    await tools.call(name, args); the envelope has id, status, retention and value.
    All results are saved automatically. Inspect previous results using
    await session.results.list({after: 0, limit: 50}), get(id), or
    read({reference: id, offset: 0, limit: 8000, query: 'literal'}).
    Emit text(value), image(imageUrlOrBlock), or file(fileBlock) to the model.
    Discover saved cell outcomes with await session.cells.list({after: 0, limit: 50}).
    No filesystem, network, imports,
    process, timers or persistent JS globals. Use existing tools for workspace IO.
    Calls still require their normal approval. Await calls, including Promise.all.
    Results larger than the transport limit return a reference for paged reading.
    """
    dispatcher = await Dispatcher.create(ctx)
    return format_result(await cells.start(dispatcher, code, yield_time_ms))


# The container itself grants no workspace capability; every nested tool is reviewed.
setattr(exec_javascript, "_orchestrator", True)


@function_tool(is_enabled=runtime_available, failure_error_function=orchestration_error)
async def wait_javascript(
    ctx: RunContextWrapper[Any], cell_id: str, timeout_ms: int = 1000, offset: int = 0
) -> Any:
    """Wait up to 30000 ms for a live JS cell; offset avoids repeating emitted text.

    Completed outputs survive restart. Interrupted JavaScript is never replayed.
    Keep waiting for running cells before ending the agent run.
    """
    dispatcher = await Dispatcher.create(ctx)
    return format_result(await cells.wait(dispatcher, cell_id, timeout_ms, offset))


@function_tool(is_enabled=runtime_available, failure_error_function=orchestration_error)
async def cancel_javascript(ctx: RunContextWrapper[Any], cell_id: str) -> Any:
    """Cancel a live JS cell and its pending tool requests; retain completed results."""
    dispatcher = await Dispatcher.create(ctx)
    return format_result(await cells.cancel(dispatcher, cell_id))


setattr(wait_javascript, "_orchestrator", True)
setattr(cancel_javascript, "_orchestrator", True)
