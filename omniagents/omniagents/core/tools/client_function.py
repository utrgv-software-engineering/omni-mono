"""``@client_function_tool`` — declare a tool whose implementation lives
on the connected client (CLI, launcher, web UI) rather than in the agent
process.

Usage::

    @client_function_tool
    async def notify(ctx, message: str) -> dict:
        '''Send a heads-up to the human operator. Does not pause the run.'''

    @client_function_tool(expect_result=True, timeout_seconds=1800)
    async def escalate(ctx, message: str) -> dict:
        '''Pause and ask the human for input. Returns their reply.'''

The decorated function's signature, type hints, and docstring become the
tool schema that the LLM sees. The function *body* is ignored — the
decorator replaces it with one that calls
``AgentService.request_client_function(name, args, ...)``.

The dispatched ``function`` name on the wire is the Python function
name by default; override with ``name="..."``.

Clients implement handlers by subscribing to ``client_request`` events
on their RPC channel and matching on ``payload["function"]``. For
fire-and-forget calls (``expect_result=False``), the client doesn't
need to respond. For blocking calls, the client sends back a
``client_response`` RPC with ``{request_id, ok, result, error}``.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional

from .discovery import function_tool


def client_function_tool(
    func: Callable | None = None,
    *,
    name: str | None = None,
    expect_result: bool = False,
    timeout_seconds: float | None = None,
    running_summary: str | None = None,
):
    """Decorate a tool declaration to dispatch via ``request_client_function``.

    Tools created with this decorator are conventionally **hidden from the
    transcript** — the client receives a ``client_request`` event and
    paints the user-visible side-effect itself (toast, banner, dialog,
    etc.). Clients identify these tools by checking whether the called
    tool name matches one of their registered client-function handlers.

    Args:
        name: Wire-name of the client function. Defaults to the Python
            function name. Clients match on this string to route the call
            to their handler.
        expect_result: When True, the tool awaits a ``client_response``
            from the client and returns the reply. Use for tools that
            need user input back (escalate, prompt, approve). When False,
            the call is fire-and-forget and the tool returns immediately.
        timeout_seconds: Wall-clock timeout for ``expect_result=True``.
            None means wait forever (until the client responds or the run
            is cancelled).
        running_summary: Optional human phrasing for the in-flight tool
            call, shown as the tool card's title while it runs (proxied
            through ``function_tool``).
    """

    def decorator(f: Callable):
        sig = inspect.signature(f)
        params = list(sig.parameters.values())
        if not params or params[0].name != "ctx":
            raise ValueError(
                f"@client_function_tool: '{f.__name__}' must have 'ctx' as "
                f"its first parameter (got {[p.name for p in params]})"
            )

        dispatch_name = name or f.__name__
        timeout_ms = int(timeout_seconds * 1000) if timeout_seconds else None
        expect = bool(expect_result)

        # functools.wraps copies metadata AND sets ``__wrapped__`` on the
        # wrapper. ``typing.get_type_hints`` (used by the agents SDK to
        # generate the tool's JSON schema) unwraps ``__wrapped__`` so
        # forward refs like ``RunContextWrapper[Any]`` resolve against
        # the *user's* module globals — without this, schema generation
        # blows up because RunContextWrapper isn't imported in this file.
        @functools.wraps(f)
        async def _wrapper(*args, **kwargs):
            # Bind incoming args (positional + keyword) so we can extract
            # the call_args dict the SDK gives us regardless of how the
            # tool is invoked.
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            ctx = bound.arguments.pop("ctx", None)
            call_args = {k: v for k, v in bound.arguments.items()}

            from omniagents.core.agents.service import current_service
            from omniagents.core.session.helpers import session_from_ctx

            svc = current_service()
            if svc is None:
                return {"ok": False, "error": "no agent service running"}

            session = session_from_ctx(ctx)

            try:
                raw = await svc.request_client_function(
                    dispatch_name,
                    call_args,
                    session=session,
                    expect_result=expect,
                    timeout_ms=timeout_ms,
                )
            except Exception as e:
                return {"ok": False, "error": f"client dispatch failed: {e}"}

            if not expect:
                return {"ok": True, "delivered": bool(raw)}
            if raw is None:
                return {
                    "ok": False,
                    "reason": "no client response within timeout",
                }
            if isinstance(raw, dict):
                if raw.get("ok") is False:
                    return {
                        "ok": False,
                        "error": raw.get("error") or "client rejected",
                    }
                result = raw.get("result")
                if (
                    dispatch_name == "escalate"
                    and isinstance(result, dict)
                    and result.get("input_content")
                ):
                    return human_reply_outputs(result)
                return {"ok": True, "result": result}
            return {"ok": True, "result": raw}

        # ``functools.wraps`` set __name__, __doc__, __qualname__,
        # __module__, __wrapped__, __annotations__, __dict__. We also
        # carry the explicit Signature so introspectors that prefer
        # __signature__ over recomputing from __wrapped__ get the right
        # parameter list.
        _wrapper.__signature__ = sig  # type: ignore[attr-defined]

        ft_kwargs: dict = {}
        if running_summary:
            ft_kwargs["running_summary"] = running_summary
        tool = function_tool(_wrapper, **ft_kwargs)
        try:
            setattr(tool, "_is_client_function_tool", True)
            setattr(tool, "_client_function_name", dispatch_name)
            setattr(tool, "_client_function_expects_result", expect)
        except Exception:
            pass
        return tool

    if func is None:
        return decorator
    return decorator(func)


__all__ = ["client_function_tool"]


def human_reply_outputs(result: dict) -> list:
    """Keep human attachments multimodal through the SDK tool-result boundary."""
    from agents.tool import ToolOutputText, ToolOutputImage, ToolOutputFileContent

    outputs = []
    for part in result["input_content"]:
        kind = part.get("type")
        if kind == "input_text":
            outputs.append(ToolOutputText(text=part["text"]))
        elif kind == "input_image":
            outputs.append(
                ToolOutputImage(
                    image_url=part["image_url"], detail=part.get("detail", "auto")
                )
            )
        elif kind == "input_file":
            outputs.append(
                ToolOutputFileContent(
                    file_data=part.get("file_data"), filename=part.get("filename")
                )
            )
        else:
            raise ValueError(f"Unsupported human reply content: {kind}")
    return outputs
