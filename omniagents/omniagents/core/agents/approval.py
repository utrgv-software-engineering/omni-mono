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

from omniagents.core.agents.mcp_identity import stamp_mcp_tool_identities

NeedsApprovalCallable = Callable[[Any, dict, str], Awaitable[bool]]


def session_allows_tool(session: Any, tool_name: str) -> bool:
    """True when the session's always-allow state covers ``tool_name``.

    Consulted by the per-tool ``needs_approval`` predicate below and by the
    bridge's interruption loop — the SDK raises interruptions for all
    parallel tool calls *before* the first prompt is answered, so the loop
    must re-check as the allow-set grows mid-batch (an "Always" on the
    first call silently approves the rest).
    """
    try:
        allow_names = getattr(session, "always_allow_tools", None)
        if allow_names and tool_name in allow_names:
            return True
    except Exception:
        pass
    try:
        allow_patterns = getattr(session, "always_allow_patterns", None)
        if allow_patterns:
            for pattern in allow_patterns:
                try:
                    if re.match(pattern, tool_name):
                        return True
                except re.error:
                    continue
    except Exception:
        pass
    return False


def session_allows_mcp_tool(session: Any, server_name: Any, tool_name: Any) -> bool:
    """True when the session's tuple-keyed MCP allow-set covers the tool.

    Keys mirror ``AgentService._mcp_allow_key`` normalization (stripped
    strings) so grants written by ``safe_tool_overrides.safe_mcp_tools``
    and by "Always" clicks match here.
    """
    server = str(server_name or "").strip()
    tool = str(tool_name or "").strip()
    if not server or not tool:
        return False
    try:
        allow_set = getattr(session, "always_allow_mcp_tools", None)
        if not allow_set:
            return False
        return (server, tool) in allow_set
    except Exception:
        return False


def _make_mcp_predicate(
    identity: tuple[str, str],
    public_name: str,
    session: Any,
    spec_names: set[str],
    spec_patterns: List[Pattern],
    skip_approvals: bool,
) -> NeedsApprovalCallable:
    """``needs_approval`` for an MCP-derived tool.

    Trust is keyed by the ``(server, tool)`` identity tuple — never by
    parsing the flat public name — so a grant for one server's tool can't
    cover a same-named tool on another server. Spec-level safe names and
    patterns still match the public name (autopilot's ``.*`` keeps
    covering everything).
    """

    async def needs_approval(
        ctx: Any, args: dict, call_id: str
    ) -> bool:  # noqa: ANN401
        if skip_approvals:
            return False
        if session_allows_mcp_tool(session, identity[0], identity[1]):
            return False
        if public_name in spec_names:
            return False
        for pattern in spec_patterns:
            if pattern.match(public_name):
                return False
        return True

    return needs_approval


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
        if session_allows_tool(session, tool_name):
            return False

        if tool_name in spec_names:
            return False
        for pattern in spec_patterns:
            if pattern.match(tool_name):
                return False
        return True

    return needs_approval


def attach_tool_approval_predicates(
    tools: Iterable[Any],
    *,
    session: Any,
    safe_tool_names: Iterable[str] | None = None,
    safe_tool_patterns: Iterable[str] | None = None,
    skip_approvals: bool = False,
    exempt: Iterable[Any] = (),
) -> None:
    """Attach per-tool ``needs_approval`` predicates to a bare tool list.

    The realtime (voice) pipeline builds its ``RealtimeAgent`` from a flat
    tool list rather than through :func:`configure_tool_approval`'s agent
    walk, but the SDK's realtime session honors the same ``needs_approval``
    contract (``RealtimeSession._maybe_request_tool_approval``). Tools in
    ``exempt`` (bridged client-tool proxies, already safe-only by
    construction) and tools with an explicit ``needs_approval`` are left
    untouched.
    """

    spec_names: set[str] = set(safe_tool_names or [])
    spec_patterns: List[Pattern] = [re.compile(p) for p in (safe_tool_patterns or [])]
    exempt_ids = {id(t) for t in exempt}
    for tool in tools:
        if not isinstance(tool, FunctionTool) or id(tool) in exempt_ids:
            continue
        if tool.needs_approval not in (False, None):
            continue
        tool.needs_approval = _make_predicate(
            tool.name, session, spec_names, spec_patterns, skip_approvals
        )


def _walk_agents(root: Any) -> Iterable[Any]:
    """Yield ``root`` and every agent reachable via ``handoffs``.

    ``Agent.handoffs`` may contain bare ``Agent`` instances (the common case
    when handoffs are declared inline) or ``Handoff`` wrapper objects with
    an ``agent`` / ``_agent`` reference.
    """

    seen: set[int] = set()
    stack: List[Any] = [root]
    while stack:
        agent = stack.pop()
        if agent is None or id(agent) in seen:
            continue
        seen.add(id(agent))
        yield agent
        for handoff in getattr(agent, "handoffs", []) or []:
            target = (
                getattr(handoff, "agent", None)
                or getattr(handoff, "_agent", None)
                or handoff  # bare Agent
            )
            if target is not None:
                stack.append(target)


def _make_attacher(
    session: Any,
    spec_names: set[str],
    spec_patterns: List[Pattern],
    skip_approvals: bool,
) -> Callable[[Any], None]:
    """The per-tool attach step shared by every approval entry point."""

    def _attach(tool: Any) -> None:
        if not isinstance(tool, FunctionTool):
            return
        if tool.needs_approval not in (False, None):
            return
        identity = getattr(tool, "_mcp_identity", None)
        if identity is not None:
            tool.needs_approval = _make_mcp_predicate(
                identity, tool.name, session, spec_names, spec_patterns, skip_approvals
            )
        else:
            tool.needs_approval = _make_predicate(
                tool.name, session, spec_names, spec_patterns, skip_approvals
            )

    return _attach


def _wrap_get_all_tools(
    agent: Any, attach: Callable[[Any], None], session: Any
) -> None:
    """Patch ``agent.get_all_tools`` so lazily-resolved tools are gated.

    Both agent flavors resolve MCP tools inside ``get_all_tools`` on every
    run — after any pass over the static ``tools`` list — so this is the
    only point where they can be reached.
    """
    original = getattr(agent, "get_all_tools", None)
    if original is None or getattr(agent, "_omni_approval_patched", False):
        return

    async def _patched(run_context, *, _orig=original, _agent=agent):  # type: ignore[no-untyped-def]
        tools = await _orig(run_context)
        # Pair MCP-derived tools with their (server, original) identity
        # BEFORE attaching predicates — the attacher branches on it.
        try:
            await stamp_mcp_tool_identities(_agent, run_context, tools, session)
        except Exception:
            pass
        for t in tools:
            attach(t)
        return tools

    try:
        agent.get_all_tools = _patched  # type: ignore[assignment]
        setattr(agent, "_omni_approval_patched", True)
    except Exception:
        # Best-effort; some agents have read-only attributes.
        pass


def attach_lazy_tool_approval(
    agent: Any,
    *,
    session: Any,
    safe_tool_names: Iterable[str] | None = None,
    safe_tool_patterns: Iterable[str] | None = None,
    skip_approvals: bool = False,
) -> None:
    """Gate the tools an agent resolves lazily at run time (MCP).

    :func:`attach_tool_approval_predicates` only ever sees the STATIC
    tool list. MCP servers are handed to the agent and their tools are
    fetched on each run, after that pass, so without this they execute
    with no approval at all.

    The text path gets this for free from :func:`configure_tool_approval`,
    which walks the agent. Callers that build their agent themselves —
    the realtime/voice session builds a ``RealtimeAgent`` directly — must
    call this once the agent exists. Reaches handoff targets too, since
    each carries its own MCP servers.
    """
    attach = _make_attacher(
        session,
        set(safe_tool_names or []),
        [re.compile(p) for p in (safe_tool_patterns or [])],
        skip_approvals,
    )
    for sub_agent in _walk_agents(agent):
        _wrap_get_all_tools(sub_agent, attach, session)


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
    lazily at run time) are configured as well. For ``SandboxAgent`` runs,
    wraps each capability's ``tools()`` method too, because sandbox runtime
    appends those tools after the normal agent build/configuration pass.
    """

    spec_names: set[str] = set(safe_tool_names or [])
    spec_patterns: List[Pattern] = [re.compile(p) for p in (safe_tool_patterns or [])]

    _attach = _make_attacher(session, spec_names, spec_patterns, skip_approvals)

    for sub_agent in _walk_agents(agent):
        for tool in getattr(sub_agent, "tools", None) or []:
            _attach(tool)

        for capability in getattr(sub_agent, "capabilities", None) or []:
            original_tools = getattr(capability, "tools", None)
            if original_tools is None or not callable(original_tools):
                continue
            if getattr(capability, "_omni_approval_patched", False):
                continue

            def _patched_capability_tools(*args, _orig=original_tools, **kwargs):  # type: ignore[no-untyped-def]
                tools = list(_orig(*args, **kwargs))
                for t in tools:
                    _attach(t)
                return tools

            try:
                setattr(capability, "tools", _patched_capability_tools)
                setattr(capability, "_omni_approval_patched", True)
            except Exception:
                try:
                    object.__setattr__(capability, "tools", _patched_capability_tools)
                    object.__setattr__(capability, "_omni_approval_patched", True)
                except Exception:
                    pass

        _wrap_get_all_tools(sub_agent, _attach, session)
