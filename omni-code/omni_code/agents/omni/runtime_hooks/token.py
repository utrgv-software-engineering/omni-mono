"""Proactive compaction: when input-token utilization crosses 0.9, record
the pressure flags under ``session.variables["omni_code"]`` and run
`/compact` (checkpoint compaction) before the context overflows. The hook
body is the framework's; this module just registers it under omni-code's
state key."""

from __future__ import annotations

from omniagents import runtime_hook
from omniagents.core.runtime.compaction import make_auto_compact_token_hook

on_token = runtime_hook("token")(
    make_auto_compact_token_hook(state_key="omni_code", command="compact")
)
