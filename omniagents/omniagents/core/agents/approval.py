"""Tool-approval helpers built on the openai-agents SDK 0.16 interruption model.

Replaces the legacy ``SafeAgent`` wrapper. Approval is now driven by:

* :attr:`agents.tool.FunctionTool.needs_approval` — a callable predicate the
  SDK consults before invoking a tool. When it returns ``True``, the SDK
  emits a :class:`agents.items.ToolApprovalItem` interruption and pauses
  the run.
* :class:`agents.run_state.RunState.approve` / ``reject`` — applied by the
  server when the user responds; the run resumes via
  ``Runner.run_streamed(agent, state, ...)``.

The SDK calls ``needs_approval(ctx, parsed_args_dict, call_id)``, so each
tool gets its own predicate that closes over its name and the session.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Awaitable, Iterable, List, Pattern

from agents.tool import FunctionTool

NeedsApprovalCallable = Callable[[Any, dict, str], Awaitable[bool]]


def _make_predicate(
    tool_name: str,
    session: Any,
    spec_names: set[str],
    spec_patterns: List[Pattern],
    skip_approvals: bool,
) -> NeedsApprovalCallable:
    """Build a per-tool ``needs_approval`` predicate."""

    async def needs_approval(
        ctx: Any, args: dict, call_id: str
    ) -> bool:  # noqa: ANN401
        if skip_approvals:
            return False

        # Session-level always-allow set, mutated by the server when the
        # user clicks "Always" on a prompt.
        try:
            allow_names = getattr(session, "always_allow_tools", None)
            if allow_names and tool_name in allow_names:
                return False
        except Exception:
            pass
        try:
            allow_patterns = getattr(session, "always_allow_patterns", None)
            if allow_patterns:
                for pattern in allow_patterns:
                    try:
                        if re.match(pattern, tool_name):
                            return False
                    except re.error:
                        continue
        except Exception:
            pass

        if tool_name in spec_names:
            return False
        for pattern in spec_patterns:
            if pattern.match(tool_name):
                return False
        return True

    return needs_approval


def _walk_agents(root: Any) -> Iterable[Any]:
    """Yield ``root`` and every agent reachable via ``handoffs``."""

    seen: set[int] = set()
    stack: List[Any] = [root]
    while stack:
        agent = stack.pop()
        if agent is None or id(agent) in seen:
            continue
        seen.add(id(agent))
        yield agent
        for handoff in getattr(agent, "handoffs", []) or []:
            target = getattr(handoff, "agent", None) or getattr(handoff, "_agent", None)
            if target is not None:
                stack.append(target)


def configure_tool_approval(
    agent: Any,
    *,
    session: Any,
    safe_tool_names: Iterable[str] | None = None,
    safe_tool_patterns: Iterable[str] | None = None,
    skip_approvals: bool = False,
) -> None:
    """Attach per-tool ``needs_approval`` predicates to every FunctionTool
    in ``agent`` and any agent reachable through its handoffs.

    Tools that already declare an explicit ``needs_approval`` (anything
    other than the default ``False``) are left untouched, so per-tool
    overrides remain authoritative.

    Also wraps ``Agent.get_all_tools`` so MCP-fetched tools (resolved
    lazily at run time) are configured as well.
    """

    spec_names: set[str] = set(safe_tool_names or [])
    spec_patterns: List[Pattern] = [re.compile(p) for p in (safe_tool_patterns or [])]

    def _attach(tool: FunctionTool) -> None:
        if not isinstance(tool, FunctionTool):
            return
        if tool.needs_approval not in (False, None):
            return
        tool.needs_approval = _make_predicate(
            tool.name, session, spec_names, spec_patterns, skip_approvals
        )

    for sub_agent in _walk_agents(agent):
        for tool in getattr(sub_agent, "tools", None) or []:
            _attach(tool)

        original = getattr(sub_agent, "get_all_tools", None)
        if original is None or getattr(sub_agent, "_omni_approval_patched", False):
            continue

        async def _patched(run_context, *, _orig=original):  # type: ignore[no-redef]
            tools = await _orig(run_context)
            for t in tools:
                _attach(t)
            return tools

        try:
            sub_agent.get_all_tools = _patched  # type: ignore[assignment]
            setattr(sub_agent, "_omni_approval_patched", True)
        except Exception:
            # Best-effort; some agents have read-only attributes.
            pass
