from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class EvalContext:
    session_id: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    agent: Any | None = None
    available_tools: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)


def allowed_tool_names(agent: Any | None = None, tools: Optional[List[Any]] = None) -> Set[str]:
    tool_objs = tools if tools is not None else getattr(agent, "tools", []) if agent is not None else []
    names: Set[str] = set()
    for t in tool_objs or []:
        n = getattr(t, "name", None)
        if not n:
            n = getattr(getattr(t, "func", None), "__name__", None) or getattr(t, "__name__", None)
        if n:
            names.add(str(n))
    return names


def extract_tool_calls(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for idx, item in enumerate(history or []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        t = item.get("type")
        if name and (t is None or isinstance(t, str) and ("function_call" in t or "tool_call" in t)):
            calls.append({
                "index": idx,
                "name": str(name),
                "arguments": item.get("arguments"),
                "call_id": item.get("call_id"),
                "raw": item,
            })
    return calls


def build_eval_context(
    *,
    session: Any | None = None,
    agent: Any | None = None,
    history: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> EvalContext:
    hist = history if history is not None else (getattr(session, "history", []) if session is not None else [])
    avail = allowed_tool_names(agent, tools)
    return EvalContext(
        session_id=getattr(session, "id", None) if session is not None else None,
        history=list(hist or []),
        agent=agent,
        available_tools=avail,
        metadata=metadata or {},
    )

