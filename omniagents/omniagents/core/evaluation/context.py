from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .assertions import parse_tool_calls, parse_tool_outputs


def _entry_index(value: Any) -> int:
    if value is None:
        return -1
    idx = getattr(value, "index", None)
    if isinstance(idx, int):
        return idx
    if isinstance(value, dict):
        idx = value.get("index")
        if isinstance(idx, int):
            return idx
    return -1


@dataclass
class TimelineEntry:
    index: int

    def after(self, other: Any) -> bool:
        return self.index > _entry_index(other)

    def before(self, other: Any) -> bool:
        other_index = _entry_index(other)
        return other_index != -1 and self.index < other_index


@dataclass
class ToolCall(TimelineEntry):
    name: Optional[str] = None
    args: Any = None
    call_id: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    output: Optional["ToolOutput"] = None

    @property
    def results(self) -> Any:
        return self.output.results if self.output else None

    @property
    def results_count(self) -> int:
        results = self.results
        if isinstance(results, list):
            return len(results)
        return 0

    def has_results(self) -> bool:
        return self.results_count > 0

    def has_multiple_results(self) -> bool:
        return self.results_count > 1


@dataclass
class ToolOutput(TimelineEntry):
    call_id: Optional[str] = None
    output: Any = None
    output_json: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def results(self) -> Any:
        if isinstance(self.output_json, dict):
            return self.output_json.get("results")
        return None

    @property
    def results_count(self) -> int:
        results = self.results
        if isinstance(results, list):
            return len(results)
        return 0


@dataclass
class ToolLink:
    call: ToolCall
    output: Optional[ToolOutput] = None

    @property
    def results(self) -> Any:
        return self.output.results if self.output else None


@dataclass
class Message(TimelineEntry):
    role: Optional[str] = None
    content: Any = None
    text: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JudgeResult:
    answer: str
    reasoning: str
    raw: Dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.answer.lower() == "pass"

    def to_result(self) -> Dict[str, Any]:
        if self.passed:
            return pass_reason(reason=self.reasoning)
        return fail_reason(self.reasoning)


@dataclass
class EvalContext:
    session_id: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    agent: Any | None = None
    available_tools: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)
    _tool_calls: List[ToolCall] = field(default_factory=list, repr=False)
    _tool_outputs: List[ToolOutput] = field(default_factory=list, repr=False)
    _tool_links: Dict[str, ToolLink] = field(default_factory=dict, repr=False)
    _messages: List[Message] = field(default_factory=list, repr=False)
    _assistant_messages: List[Message] = field(default_factory=list, repr=False)
    _user_messages: List[Message] = field(default_factory=list, repr=False)
    final_assistant_message: Optional[Message] = None

    def tool_calls(self, name: Optional[str] = None) -> List[ToolCall]:
        if name is None:
            return list(self._tool_calls)
        return [c for c in self._tool_calls if c.name == name]

    def tool_results(self, name: Optional[str] = None) -> List[ToolOutput]:
        if name is None:
            return list(self._tool_outputs)
        results: List[ToolOutput] = []
        for call in self._tool_calls:
            if call.name == name and call.output:
                results.append(call.output)
        return results

    def tool_outputs(self, name: Optional[str] = None) -> List[ToolOutput]:
        return self.tool_results(name)

    def tool_links(self) -> List[ToolLink]:
        return list(self._tool_links.values())

    def first_tool(self, name: str) -> Optional[ToolCall]:
        for call in self._tool_calls:
            if call.name == name:
                return call
        return None

    def latest_tool(self, name: str) -> Optional[ToolCall]:
        for call in reversed(self._tool_calls):
            if call.name == name:
                return call
        return None

    def linked_tool_result(self, call: Any) -> Optional[ToolOutput]:
        if isinstance(call, ToolCall):
            return call.output
        call_id = getattr(call, "call_id", None)
        if call_id is None and isinstance(call, dict):
            call_id = call.get("call_id")
        if call_id is None:
            return None
        link = self._tool_links.get(call_id)
        if not link:
            return None
        return link.output

    @property
    def messages(self) -> List[Message]:
        return list(self._messages)

    @property
    def assistant_messages(self) -> List[Message]:
        return list(self._assistant_messages)

    @property
    def user_messages(self) -> List[Message]:
        return list(self._user_messages)

    def judge_result(self, key: str) -> Optional[JudgeResult]:
        data = self.metadata.get(key)
        if not isinstance(data, dict):
            return None
        answer = data.get("answer")
        reasoning = data.get("reasoning")
        if not isinstance(answer, str) or not isinstance(reasoning, str):
            return None
        ans = answer.strip()
        reason = reasoning.strip()
        return JudgeResult(answer=ans, reasoning=reason, raw=data)


def allowed_tool_names(
    agent: Any | None = None, tools: Optional[List[Any]] = None
) -> Set[str]:
    tool_objs = (
        tools
        if tools is not None
        else getattr(agent, "tools", []) if agent is not None else []
    )
    names: Set[str] = set()
    for t in tool_objs or []:
        n = getattr(t, "name", None)
        if not n:
            n = getattr(getattr(t, "func", None), "__name__", None) or getattr(
                t, "__name__", None
            )
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
        if name and (
            t is None
            or isinstance(t, str)
            and ("function_call" in t or "tool_call" in t)
        ):
            calls.append(
                {
                    "index": idx,
                    "name": str(name),
                    "arguments": item.get("arguments"),
                    "call_id": item.get("call_id"),
                    "raw": item,
                }
            )
    return calls


def build_eval_context(
    *,
    session: Any | None = None,
    agent: Any | None = None,
    history: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> EvalContext:
    hist = (
        history
        if history is not None
        else (getattr(session, "history", []) if session is not None else [])
    )
    hist_list = list(hist or [])
    avail = allowed_tool_names(agent, tools)
    ctx = EvalContext(
        session_id=getattr(session, "id", None) if session is not None else None,
        history=hist_list,
        agent=agent,
        available_tools=avail,
        metadata=metadata or {},
    )
    call_dicts = parse_tool_calls(hist_list)
    output_dicts = parse_tool_outputs(hist_list)
    output_objs: List[ToolOutput] = []
    for o in output_dicts:
        raw_output = o.get("raw") if isinstance(o.get("raw"), dict) else o
        output_objs.append(
            ToolOutput(
                index=int(o.get("index", -1) or -1),
                call_id=o.get("call_id"),
                output=o.get("output"),
                output_json=o.get("output_json"),
                raw=raw_output,
            )
        )
    output_by_id = {o.call_id: o for o in output_objs if o.call_id}
    call_objs: List[ToolCall] = []
    for c in call_dicts:
        raw_call = c.get("raw") if isinstance(c.get("raw"), dict) else c
        call_id = c.get("call_id")
        call_objs.append(
            ToolCall(
                index=int(c.get("index", -1) or -1),
                name=c.get("name"),
                args=c.get("args"),
                call_id=call_id,
                raw=raw_call,
                output=output_by_id.get(call_id),
            )
        )
    tool_links: Dict[str, ToolLink] = {}
    for call in call_objs:
        if call.call_id:
            tool_links[call.call_id] = ToolLink(call=call, output=call.output)
    messages: List[Message] = []
    assistant_messages: List[Message] = []
    user_messages: List[Message] = []
    for idx, item in enumerate(hist_list):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = _extract_message_text(item)
        message = Message(
            index=idx, role=role, content=item.get("content"), text=text, raw=item
        )
        messages.append(message)
        if role == "assistant":
            assistant_messages.append(message)
        if role == "user":
            user_messages.append(message)
    ctx._tool_calls = call_objs
    ctx._tool_outputs = output_objs
    ctx._tool_links = tool_links
    ctx._messages = messages
    ctx._assistant_messages = assistant_messages
    ctx._user_messages = user_messages
    ctx.final_assistant_message = assistant_messages[-1] if assistant_messages else None
    return ctx


def _extract_message_text(item: Dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        txt = content.strip()
        return txt
    if isinstance(content, list) and item.get("type") == "message":
        builder: List[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    builder.append(text)
        if builder:
            return "".join(builder).strip()
    return ""


def pass_reason(reason: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"passed": True}
    if reason is not None:
        result["reason"] = reason
    if extra:
        result.update(extra)
    return result


def fail_reason(reason: str, **extra: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {"passed": False, "reason": reason}
    if extra:
        result.update(extra)
    return result
