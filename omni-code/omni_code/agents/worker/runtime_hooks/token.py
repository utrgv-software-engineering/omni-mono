"""Proactive compaction for workers: when input-token utilization crosses
the threshold, record the pressure flags and run `/compact` before the
context overflows. Same wiring as the omni agent's hook."""

from __future__ import annotations

from omniagents import runtime_hook
from omniagents.core.runtime.compaction import make_auto_compact_token_hook

on_token = runtime_hook("token")(
    make_auto_compact_token_hook(state_key="omni_code", command="compact")
)
