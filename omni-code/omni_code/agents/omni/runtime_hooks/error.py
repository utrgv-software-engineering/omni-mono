"""Reactive compaction: on a context-length-exceeded run error, record the
pressure flags under ``session.variables["omni_code"]``, run `/compact`
(checkpoint compaction), and retry the run. The hook body is the
framework's; this module just registers it under omni-code's state key."""

from __future__ import annotations

from omniagents import runtime_hook
from omniagents.core.runtime.compaction import make_auto_compact_error_hook

on_error = runtime_hook("error")(
    make_auto_compact_error_hook(state_key="omni_code", command="compact")
)
