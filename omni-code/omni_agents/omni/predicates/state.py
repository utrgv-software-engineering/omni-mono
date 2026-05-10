"""is_enabled predicates for state-dependent omni tools.

These predicates hide tools from the LLM's available-tool list when
there is nothing for them to act on (no background bash jobs, no
spawned workers). The SDK calls each predicate before each model
turn with ``(RunContextWrapper, Agent)``; a False return drops the
tool from the schema sent to the model that turn.

Hiding the tool is preferable to letting the LLM call it and
no-op — saves a turn and avoids a confused-looking transcript
(``"tool returned: no jobs running"``).
"""

from __future__ import annotations

from typing import Any

from omniagents.core.predicates.discovery import is_enabled_predicate


@is_enabled_predicate
def any_bash_jobs_exist(ctx: Any, agent: Any) -> bool:
    """True iff at least one background bash job exists in the registry."""
    try:
        from tools._bash_jobs import get_registry
    except Exception:
        return False
    try:
        return len(get_registry().list_all()) > 0
    except Exception:
        return False


@is_enabled_predicate
def any_workers_exist(ctx: Any, agent: Any) -> bool:
    """True iff at least one worker has been spawned in this process."""
    try:
        from tools.worker_tools import _WORKERS
    except Exception:
        return False
    try:
        return len(_WORKERS) > 0
    except Exception:
        return False
