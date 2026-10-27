"""What every model call used: one record per model response, wherever it ran.

Every model call the Agents SDK makes (a run's turns, the agents it calls as
tools, the guardian's reviews, judges, utility runs) ends a ``response`` span
in its trace. :class:`UsageRecorder` is a trace processor that turns each one
into a record:

- the tokens: input, of which cached and cache writes, and output, of which
  reasoning;
- the model;
- the agent whose turn it was (the nearest agent span);
- the trace's group (``session_<id>``: the run's session, see the bridge and
  ``runtime.ephemeral``) and its metadata.

It is a *permanent* processor: :func:`omniagents.core.tracing.set_processors`
keeps it whatever tracing provider is configured, so usage is recorded with
tracing off too (nothing is exported unless a provider says so).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = ["UsageRecorder", "response_usage", "session_of_group"]

#: Told each record (see the module docstring); must not block for long.
Sink = Callable[[Dict[str, Any]], None]


def session_of_group(group_id: Optional[str]) -> Optional[str]:
    """``session_<id>`` (a trace group) -> ``<id>``."""
    if group_id and group_id.startswith("session_"):
        return group_id[len("session_") :]
    return None


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def response_usage(usage: Any) -> Dict[str, int]:
    """A response's ``usage`` (the SDK's object or its dict) as flat token counts."""
    inputs = _get(usage, "input_tokens_details")
    outputs = _get(usage, "output_tokens_details")
    return {
        "input_tokens": int(_get(usage, "input_tokens") or 0),
        "cached_tokens": int(_get(inputs, "cached_tokens") or 0),
        "cache_write_tokens": int(_get(inputs, "cache_write_tokens") or 0),
        "output_tokens": int(_get(usage, "output_tokens") or 0),
        "reasoning_tokens": int(_get(outputs, "reasoning_tokens") or 0),
    }


class UsageRecorder:
    """A trace processor: each finished model response, to ``sink``."""

    #: How many traces' groups, and spans' parents, are remembered. A trace is
    #: not forgotten when it reports its end: a run's trace can report one while
    #: its spans go on (the guardian's review ends a trace of its own mid-run), so
    #: the oldest are dropped instead.
    REMEMBERED_TRACES = 4096
    REMEMBERED_SPANS = 65536

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self._lock = threading.Lock()
        #: trace id -> (group id, metadata, workflow name)
        self._traces: "OrderedDict[str, tuple]" = OrderedDict()
        #: span id -> (parent span id, agent name or None, trace id)
        self._spans: "OrderedDict[str, tuple]" = OrderedDict()

    # -- TracingProcessor ---------------------------------------------------------

    def on_trace_start(self, trace: Any) -> None:
        with self._lock:
            if trace.trace_id not in self._traces:
                self._traces[trace.trace_id] = (
                    getattr(trace, "group_id", None),
                    dict(getattr(trace, "metadata", None) or {}),
                    getattr(trace, "name", None),
                )
            while len(self._traces) > self.REMEMBERED_TRACES:
                self._traces.popitem(last=False)

    def on_trace_end(self, trace: Any) -> None:
        pass

    def on_span_start(self, span: Any) -> None:
        data = span.span_data
        agent = data.name if getattr(data, "type", None) == "agent" else None
        with self._lock:
            self._spans[span.span_id] = (span.parent_id, agent, span.trace_id)
            while len(self._spans) > self.REMEMBERED_SPANS:
                self._spans.popitem(last=False)

    def on_span_end(self, span: Any) -> None:
        data = span.span_data
        if getattr(data, "type", None) != "response":
            return
        response = getattr(data, "response", None)
        usage = _get(response, "usage") or getattr(data, "usage", None)
        if usage is None:
            return
        with self._lock:
            group, metadata, workflow = self._traces.get(
                span.trace_id, (None, {}, None)
            )
            agent = None
            parent = span.parent_id
            seen = 0
            while parent and seen < 64:
                parent_of, name, _ = self._spans.get(parent, (None, None, None))
                if name:
                    agent = name
                    break
                parent, seen = parent_of, seen + 1
        record = {
            "at": time.time(),
            "trace_id": span.trace_id,
            "group_id": group,
            "session_id": session_of_group(group),
            "workflow": workflow,
            "metadata": metadata,
            "agent": agent,
            "model": _get(response, "model"),
            **response_usage(usage),
        }
        try:
            self.sink(record)
        except Exception:
            logger.exception("usage: recording a model response failed")

    def shutdown(self) -> None:
        pass

    def force_flush(self) -> None:
        pass
